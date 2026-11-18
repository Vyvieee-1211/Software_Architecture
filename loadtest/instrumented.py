"""Điểm chạy API cho đo tải; thêm log lời gọi mock refund, không đổi xử lý nghiệp vụ."""
import logging
import os

from app.integrations.mock_payment import MockPaymentGateway

original_refund = MockPaymentGateway.refund


def observed_refund(self, payment_id, *args, **kwargs):
    logging.getLogger("uvicorn.error").info("loadtest_mock_refund payment_id=%s", payment_id)
    return original_refund(self, payment_id, *args, **kwargs)


MockPaymentGateway.refund = observed_refund

resilience_log = logging.getLogger("app.repositories.resilience")
resilience_log.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
resilience_log.addHandler(handler)
resilience_log.propagate = False

from app.main import app  # noqa: E402


if os.getenv("LOADTEST_DIAGNOSTICS") == "1":
    import asyncio
    from collections import Counter
    import faulthandler
    from anyio.to_thread import current_default_thread_limiter
    from app.repositories.database import engine

    faulthandler.enable()
    faulthandler.dump_traceback_later(10, repeat=True)
    original_app = app

    async def sample_waits():
        while True:
            await asyncio.sleep(5)
            stages = Counter()
            for task in asyncio.all_tasks():
                coroutine = task.get_coro()
                seen = set()
                while coroutine is not None and id(coroutine) not in seen:
                    seen.add(id(coroutine))
                    code = getattr(coroutine, "cr_code", None)
                    if code and code.co_name in {"serialize_response", "run_endpoint_function", "solve_dependencies"}:
                        stages[code.co_name] += 1
                    coroutine = getattr(coroutine, "cr_await", None) or getattr(coroutine, "gi_yieldfrom", None)
            limiter = current_default_thread_limiter()
            logging.getLogger("uvicorn.error").info(
                "loadtest_waits borrowed=%s total=%s checked_out=%s stages=%s",
                limiter.borrowed_tokens, limiter.total_tokens, engine.pool.checkedout(), dict(stages))

    class DiagnosticApp:
        """Chỉ bật cho lượt S1 chẩn đoán, giữ nguyên ứng dụng và nghiệp vụ bên trong."""
        async def __call__(self, scope, receive, send):
            if scope["type"] == "lifespan":
                sampler = asyncio.create_task(sample_waits())
                try:
                    await original_app(scope, receive, send)
                finally:
                    sampler.cancel()
                    faulthandler.cancel_dump_traceback_later()
            else:
                await original_app(scope, receive, send)

    app = DiagnosticApp()

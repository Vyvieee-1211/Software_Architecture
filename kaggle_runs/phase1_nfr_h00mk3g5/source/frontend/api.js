const SESSION_KEY = 'concert.session';
let session = null;

try {
  const saved = JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null');
  if (saved && typeof saved.token === 'string' && typeof saved.email === 'string') session = saved;
} catch {
  // The app still works for this page when browser storage is unavailable.
}

export function getSession() { return session; }

export function setSession(value) {
  session = value;
  try {
    if (value) sessionStorage.setItem(SESSION_KEY, JSON.stringify(value));
    else sessionStorage.removeItem(SESSION_KEY);
  } catch { /* Keep the current session in memory. */ }
}

export class ApiError extends Error {
  constructor(message, status = 0, code = '') {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

const messages = {
  EmailAlreadyExists: 'Email này đã được đăng ký. Bạn hãy đăng nhập hoặc dùng email khác.',
  InvalidCredentials: 'Email hoặc mật khẩu chưa đúng.',
  InvalidToken: 'Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.',
  SaleNotOpen: 'Concert này chưa mở bán. Vui lòng quay lại vào thời gian mở bán.',
  SoldOut: 'Số vé còn lại không đủ. Vui lòng kiểm tra lại số lượng và hạng vé.',
  Forbidden: 'Bạn không có quyền thực hiện thao tác này.',
  NotFound: 'Không tìm thấy thông tin bạn yêu cầu.',
  InvalidState: 'Đơn vé này đã được huỷ hoặc không thể thay đổi trạng thái.',
};

async function request(path, { method = 'GET', body, auth = false } = {}) {
  const token = session?.token;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(path, {
      method,
      headers: {
        Accept: 'application/json',
        ...(body ? { 'Content-Type': 'application/json' } : {}),
        ...(auth && token ? { Authorization: `Bearer ${token}` } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
      signal: controller.signal,
      cache: 'no-store',
    });
    let data;
    try {
      data = await response.json();
    } catch (error) {
      if (response.ok) throw error;
      data = null;
    }
    if (response.ok && data === null) throw new Error('Empty API response');
    if (!response.ok) {
      if (response.status === 401 && auth && token === session?.token) {
        setSession(null);
        window.dispatchEvent(new Event('session-expired'));
      }
      let message = messages[data?.error];
      if (!message && response.status === 422) message = 'Thông tin chưa hợp lệ. Vui lòng kiểm tra các trường và số lượng vé (1–10).';
      if (!message && response.status === 401) message = auth ? 'Vui lòng đăng nhập lại để tiếp tục.' : messages.InvalidCredentials;
      if (!message && response.status === 404) message = messages.NotFound;
      if (!message) message = 'Không thể thực hiện yêu cầu lúc này. Vui lòng thử lại sau.';
      throw new ApiError(message, response.status, data?.error || '');
    }
    return data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    const message = method === 'GET'
      ? 'Không thể kết nối máy chủ. Kiểm tra kết nối và thử lại.'
      : 'Kết nối bị gián đoạn. Hãy kiểm tra kết quả trước khi gửi lại yêu cầu.';
    throw new ApiError(message, 0, 'NetworkError');
  } finally {
    clearTimeout(timeout);
  }
}

export const api = {
  concerts: () => request('/concerts'),
  ticketTypes: (id) => request(`/concerts/${id}/ticket-types`),
  register: (body) => request('/auth/register', { method: 'POST', body }),
  login: (body) => request('/auth/login', { method: 'POST', body }),
  orders: () => request('/orders/me', { auth: true }),
  createOrder: (body) => request('/orders', { method: 'POST', body, auth: true }),
  cancelOrder: (id) => request(`/orders/${id}`, { method: 'DELETE', auth: true }),
};

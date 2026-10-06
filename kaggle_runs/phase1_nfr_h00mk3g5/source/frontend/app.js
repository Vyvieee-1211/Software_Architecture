import { api, getSession, setSession } from './api.js';

const main = document.querySelector('#main');
const notices = document.querySelector('#notices');
const dialog = document.querySelector('#confirm-dialog');
const confirmButton = document.querySelector('#confirm-submit');
const cancelButton = document.querySelector('#confirm-cancel');
let pageVersion = 0;
let homeConcerts = [];
let detail = null;
let orderData = [];
let ticketLookup = new Map();
let authBusy = false;
let mutationBusy = false;
let confirmAction = null;
const drafts = new Map();

const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const money = (value) => new Intl.NumberFormat('vi-VN', { style: 'currency', currency: 'VND', maximumFractionDigits: 0 }).format(Number(value));
const date = (value) => new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`);
const dateText = (value, options = {}) => new Intl.DateTimeFormat('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', ...options }).format(date(value));
const isOpen = (concert) => date(concert.sale_open_time).getTime() <= Date.now();
const saleBadge = (concert) => `<span class="badge ${isOpen(concert) ? 'badge-success' : 'badge-muted'}">${isOpen(concert) ? 'Đang mở bán' : 'Sắp mở bán'}</span>`;
const path = () => (location.hash.slice(1) || '/').split('?')[0];
const safeNext = (value) => /^\/(?:concerts\/\d+|my-orders)?$/.test(value || '') ? value : '/';
const nextPage = () => safeNext(new URLSearchParams(location.hash.split('?')[1] || '').get('next'));

function navigate(destination) {
  if (location.hash === `#${destination}`) renderRoute();
  else location.hash = destination;
}

function showNotice(message, kind = 'info') {
  notices.innerHTML = `<div class="notice notice-${kind}" role="${kind === 'error' ? 'alert' : 'status'}"><span>${escape(message)}</span><button class="button button-link button-small" data-action="dismiss-notice" aria-label="Đóng thông báo">×</button></div>`;
}

function renderHeader() {
  const session = getSession();
  document.querySelector('#account').innerHTML = session
    ? `<span class="account-email" title="${escape(session.email)}">${escape(session.email)}</span><button class="button button-secondary button-small" data-action="logout">Đăng xuất</button>`
    : '<a class="button button-secondary button-small" href="#/login">Đăng nhập</a><a class="button button-primary button-small" href="#/register">Đăng ký</a>';
  document.querySelectorAll('[data-nav]').forEach((link) => {
    const active = link.dataset.nav === 'orders' ? path() === '/my-orders' : path() === '/' || path().startsWith('/concerts/');
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
}

function loading(message = 'Đang tải thông tin…') {
  main.innerHTML = `<div class="loading-state" role="status"><span class="spinner" aria-hidden="true"></span><p>${escape(message)}</p></div>`;
}

function renderError(error) {
  main.innerHTML = `<div class="panel error-state"><h1>Chưa thể tải thông tin</h1><p role="alert">${escape(error.message)}</p><button class="button button-primary" data-action="retry">Thử lại</button></div>`;
}

function empty(title, description, action = '') {
  return `<div class="panel empty-state"><span class="empty-icon" aria-hidden="true">♫</span><h2>${escape(title)}</h2><p>${escape(description)}</p>${action}</div>`;
}

async function renderRoute() {
  const version = ++pageVersion;
  detail = null;
  if (!mutationBusy) dialog.close();
  const route = path();
  if (route === '/my-orders' && !getSession()) {
    navigate('/login?next=%2Fmy-orders');
    showNotice('Đăng nhập để xem và quản lý đơn vé của bạn.');
    return;
  }
  renderHeader();
  loading();
  try {
    if (route === '/' || route === '/concerts') await renderHome(version);
    else if (/^\/concerts\/\d+$/.test(route)) await renderDetail(version, Number(route.split('/')[2]));
    else if (route === '/login' || route === '/register') renderAuth(route === '/register');
    else if (route === '/my-orders') await renderOrders(version);
    else {
      document.title = 'Không tìm thấy trang — Concert';
      main.innerHTML = empty('Không tìm thấy trang', 'Đường dẫn này không tồn tại.', '<a class="button button-primary" href="#/">Về trang chủ</a>');
    }
  } catch (error) {
    if (version === pageVersion) renderError(error);
  }
  if (version === pageVersion) {
    main.focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: 'instant' });
  }
}

async function renderHome(version) {
  const concerts = await api.concerts();
  if (version !== pageVersion) return;
  homeConcerts = concerts;
  document.title = 'Khám phá concert — Concert';
  main.innerHTML = `
    <section class="hero" aria-labelledby="hero-title">
      <div><p class="eyebrow">HẸN NHAU GIỮA NHỮNG GIAI ĐIỆU</p><h1 id="hero-title">Âm nhạc hay hơn<br>khi có bạn ở đó.</h1><p class="lead">Tìm concert bạn yêu thích, chọn vé và sẵn sàng<br class="desktop-break"> cho những khoảnh khắc đáng nhớ.</p><button class="button button-primary" data-action="explore">Khám phá concert <span aria-hidden="true">↗</span></button></div>
      <div class="hero-mark" aria-hidden="true"><span>♫</span></div>
    </section>
    <section aria-labelledby="concerts-title" id="concert-section">
      <div class="section-heading"><div><p class="eyebrow">LỊCH HẸN ÂM NHẠC</p><h2 id="concerts-title">Khám phá concert</h2></div><span class="result-count" id="result-count" aria-live="polite"></span></div>
      <div class="toolbar"><div class="search-field"><label class="sr-only" for="search">Tìm concert hoặc nghệ sĩ</label><input id="search" type="search" placeholder="Tìm concert hoặc nghệ sĩ…" autocomplete="off"></div><div class="filter-field"><label class="sr-only" for="sale-filter">Trạng thái mở bán</label><select id="sale-filter"><option value="all">Tất cả concert</option><option value="open">Đang mở bán</option><option value="upcoming">Sắp mở bán</option></select></div></div>
      <div id="concert-results"></div>
    </section>`;
  renderConcertList();
}

function renderConcertList() {
  const query = document.querySelector('#search').value.trim().toLocaleLowerCase('vi');
  const filter = document.querySelector('#sale-filter').value;
  const concerts = homeConcerts.filter((concert) => `${concert.name} ${concert.artist || ''}`.toLocaleLowerCase('vi').includes(query) && (filter === 'all' || isOpen(concert) === (filter === 'open')));
  document.querySelector('#result-count').textContent = `${concerts.length} concert`;
  document.querySelector('#concert-results').innerHTML = concerts.length ? `<div class="concert-grid">${concerts.map((concert, index) => `
    <article class="concert-card">
      <a class="concert-art ${index % 2 ? 'art-alt' : ''}" href="#/concerts/${concert.id}" tabindex="-1" aria-hidden="true"><span class="art-label">LIVE MUSIC / ${escape(dateText(concert.start_time, { hour: undefined, minute: undefined }))}</span><span class="art-symbol">${index % 2 ? '♬' : '♫'}</span><span class="art-bottom">${escape(concert.artist || 'Live concert')}</span></a>
      <div class="card-body">${saleBadge(concert)}<h3 class="card-heading"><a href="#/concerts/${concert.id}">${escape(concert.name)}</a></h3><p class="artist-name">${escape(concert.artist || 'Nghệ sĩ đang cập nhật')}</p><div class="meta-list"><p class="meta-row"><span aria-hidden="true">◷</span>${escape(dateText(concert.start_time))}</p><p class="meta-row"><span aria-hidden="true">⌖</span>${escape(concert.venue || 'Địa điểm đang cập nhật')}</p></div><div class="card-footer"><span>${isOpen(concert) ? 'Chọn hạng vé phù hợp với bạn' : `Mở bán: ${escape(dateText(concert.sale_open_time))}`}</span><a class="button button-secondary button-small" href="#/concerts/${concert.id}" aria-label="Xem vé ${escape(concert.name)}">Xem vé <span aria-hidden="true">→</span></a></div></div>
    </article>`).join('')}</div>` : empty('Chưa tìm thấy concert', homeConcerts.length ? 'Thử từ khóa khác hoặc thay đổi bộ lọc nhé.' : 'Hiện chưa có concert. Vui lòng quay lại sau.');
}

async function renderDetail(version, id) {
  const [concerts, tickets] = await Promise.all([api.concerts(), api.ticketTypes(id)]);
  if (version !== pageVersion) return;
  const concert = concerts.find((item) => item.id === id);
  if (!concert) {
    main.innerHTML = empty('Không tìm thấy concert', 'Concert này không tồn tại.', '<a class="button button-primary" href="#/">Khám phá concert khác</a>');
    return;
  }
  const draft = drafts.get(id);
  const selected = tickets.find((ticket) => ticket.id === draft?.ticketId && ticket.remaining > 0) || tickets.find((ticket) => ticket.remaining > 0);
  detail = { concert, tickets, selectedId: selected?.id };
  document.title = `${concert.name} — Concert`;
  main.innerHTML = `<a class="back-link" href="#/">← Tất cả concert</a>
    <section class="detail-heading">${saleBadge(concert)}<h1>${escape(concert.name)}</h1><p class="lead">${escape(concert.artist || 'Nghệ sĩ đang cập nhật')}</p><div class="meta-list"><p class="meta-row"><span aria-hidden="true">◷</span>${escape(dateText(concert.start_time))}</p><p class="meta-row"><span aria-hidden="true">⌖</span>${escape(concert.venue || 'Địa điểm đang cập nhật')}</p></div></section>
    <div id="sale-notice">${!isOpen(concert) ? `<div class="notice notice-info">Vé sẽ mở bán lúc ${escape(dateText(concert.sale_open_time))} (giờ Việt Nam).</div>` : ''}</div>
    <div class="detail-grid"><section aria-labelledby="ticket-title"><div class="section-heading"><h2 id="ticket-title">Chọn hạng vé</h2><span>Mỗi đơn tối đa 10 vé</span></div><div class="ticket-list" role="radiogroup" aria-labelledby="ticket-title">${tickets.length ? tickets.map((ticket) => `<label class="ticket-option ${selected?.id === ticket.id ? 'is-selected' : ''}"><input type="radio" name="ticket" value="${ticket.id}" ${selected?.id === ticket.id ? 'checked' : ''} ${ticket.remaining <= 0 ? 'disabled' : ''}><span class="ticket-copy"><strong>${escape(ticket.name)}</strong><span class="ticket-stock">${ticket.remaining > 0 ? `Còn ${ticket.remaining.toLocaleString('vi-VN')} vé` : 'Đã hết vé'}</span></span><span class="ticket-price">${money(ticket.price)}</span></label>`).join('') : empty('Chưa có hạng vé', 'Thông tin vé sẽ được cập nhật sau.')}</div></section>
      <aside class="panel booking-panel" aria-labelledby="booking-title"><p class="eyebrow">VÉ CỦA BẠN</p><h2 id="booking-title">Thông tin đặt vé</h2><div class="summary-row"><span>Hạng vé</span><strong id="summary-ticket"></strong></div><div class="summary-row"><span>Đơn giá</span><strong id="summary-price"></strong></div><div class="field"><label for="quantity">Số lượng</label><div class="quantity-control"><button type="button" class="button button-secondary" data-action="decrease" aria-label="Giảm số lượng">−</button><input id="quantity" type="number" min="1" max="10" step="1" value="${Math.min(Math.max(1, draft?.quantity || 1), Math.min(10, selected?.remaining || 1))}" inputmode="numeric" aria-describedby="quantity-error"><button type="button" class="button button-secondary" data-action="increase" aria-label="Tăng số lượng">+</button></div><p class="form-error" id="quantity-error" hidden></p></div><div class="summary-total"><span>Tổng dự kiến</span><strong id="summary-total"></strong></div><p class="field-hint">Đơn vé được xác nhận ngay khi đặt thành công.</p><button id="book-button" class="button button-primary button-full" data-action="book" type="button"></button><p class="field-hint booking-footnote">Kiểm tra lại hạng vé và số lượng trước khi xác nhận.</p></aside>
    </div>`;
  syncBooking();
}

function syncBooking() {
  if (!detail) return;
  const ticket = detail.tickets.find((item) => item.id === detail.selectedId);
  const input = document.querySelector('#quantity');
  const quantity = Number(input.value);
  const max = Math.min(10, ticket?.remaining || 0);
  const valid = Number.isInteger(quantity) && quantity >= 1 && quantity <= max;
  input.max = String(max || 1);
  input.disabled = !max;
  document.querySelector('#summary-ticket').textContent = ticket?.name || 'Chưa có vé';
  document.querySelector('#summary-price').textContent = ticket ? money(ticket.price) : '—';
  document.querySelector('#summary-total').textContent = valid && ticket ? money(Number(ticket.price) * quantity) : '—';
  const error = document.querySelector('#quantity-error');
  error.hidden = valid || !ticket;
  error.textContent = `Vui lòng chọn từ 1 đến ${max} vé.`;
  input.setAttribute('aria-invalid', String(!valid && Boolean(ticket)));
  const open = isOpen(detail.concert);
  const badge = document.querySelector('.detail-heading .badge');
  badge.textContent = open ? 'Đang mở bán' : 'Sắp mở bán';
  badge.className = `badge ${open ? 'badge-success' : 'badge-muted'}`;
  const button = document.querySelector('#book-button');
  button.disabled = !valid || !open || !ticket;
  button.textContent = !open ? 'Chưa đến thời gian mở bán' : !ticket ? 'Chưa có vé khả dụng' : getSession() ? 'Đặt vé' : 'Đăng nhập để đặt vé';
  document.querySelector('[data-action="decrease"]').disabled = !ticket || quantity <= 1;
  document.querySelector('[data-action="increase"]').disabled = !ticket || quantity >= max;
  document.querySelectorAll('.ticket-option').forEach((label) => label.classList.toggle('is-selected', Number(label.querySelector('input').value) === detail.selectedId));
  if (open) document.querySelector('#sale-notice').innerHTML = '';
  if (valid) drafts.set(detail.concert.id, { ticketId: ticket.id, quantity });
}

function renderAuth(register) {
  document.title = `${register ? 'Đăng ký' : 'Đăng nhập'} — Concert`;
  const next = nextPage();
  main.innerHTML = `<div class="auth-layout"><section class="auth-intro"><p class="eyebrow">CHÀO MỪNG ĐẾN VỚI CONCERT</p><h1>Giai điệu yêu thích.<br>Kỷ niệm của riêng bạn.</h1><p class="lead">${register ? 'Tạo tài khoản để đặt vé và lưu lại những cuộc hẹn âm nhạc của bạn.' : 'Đăng nhập để đặt vé và quản lý những concert bạn sắp tham dự.'}</p><a class="back-link" href="#/">← Tiếp tục khám phá</a></section><section class="panel auth-card"><h2>${register ? 'Tạo tài khoản' : 'Đăng nhập'}</h2><p class="auth-subtitle">${register ? 'Bắt đầu với một vài thông tin đơn giản.' : 'Rất vui được gặp lại bạn.'}</p><form id="auth-form" data-mode="${register ? 'register' : 'login'}">
    ${register ? '<div class="field"><label for="full-name">Họ và tên <span class="optional">(không bắt buộc)</span></label><input id="full-name" name="full_name" autocomplete="name" maxlength="255" placeholder="Nguyễn Văn A"></div>' : ''}
    <div class="field"><label for="email">Email</label><input id="email" name="email" type="email" autocomplete="email" required placeholder="ban@example.com" maxlength="255"></div>
    <div class="field"><label for="password">Mật khẩu</label><div class="password-field"><input id="password" name="password" type="password" autocomplete="${register ? 'new-password' : 'current-password'}" ${register ? 'minlength="6" maxlength="72"' : 'maxlength="128"'} required placeholder="${register ? 'Ít nhất 6 ký tự' : 'Nhập mật khẩu'}"><button type="button" class="show-password" data-action="toggle-password" aria-controls="password" aria-label="Hiện mật khẩu" aria-pressed="false">Hiện</button></div></div>
    ${register ? '<div class="field"><label for="confirm-password">Xác nhận mật khẩu</label><input id="confirm-password" name="confirm_password" type="password" autocomplete="new-password" required placeholder="Nhập lại mật khẩu"></div>' : ''}
    <p class="form-error" id="auth-error" role="alert" hidden></p><button class="button button-primary button-full" id="auth-submit" type="submit">${register ? 'Tạo tài khoản' : 'Đăng nhập'}</button></form><p class="auth-switch">${register ? 'Đã có tài khoản?' : 'Chưa có tài khoản?'} <a href="#/${register ? 'login' : 'register'}?next=${encodeURIComponent(next)}">${register ? 'Đăng nhập' : 'Đăng ký ngay'}</a></p></section></div>`;
}

async function submitAuth(form) {
  if (authBusy) return;
  const register = form.dataset.mode === 'register';
  const values = new FormData(form);
  const email = values.get('email').trim();
  const password = values.get('password');
  const errorElement = document.querySelector('#auth-error');
  const button = document.querySelector('#auth-submit');
  errorElement.hidden = true;
  if (register && password !== values.get('confirm_password')) {
    errorElement.textContent = 'Mật khẩu xác nhận chưa khớp.';
    errorElement.hidden = false;
    document.querySelector('#confirm-password').focus();
    return;
  }
  if (new TextEncoder().encode(password).length > 72) {
    errorElement.textContent = 'Mật khẩu quá dài. Vui lòng dùng mật khẩu ngắn hơn.';
    errorElement.hidden = false;
    document.querySelector('#password').focus();
    return;
  }
  authBusy = true;
  button.disabled = true;
  button.textContent = 'Đang xử lý…';
  const version = pageVersion;
  const next = nextPage();
  try {
    if (register) {
      await api.register({ email, password, full_name: values.get('full_name').trim() || null });
      if (version !== pageVersion) return;
      navigate(`/login?next=${encodeURIComponent(next)}`);
      showNotice('Tạo tài khoản thành công. Hãy đăng nhập để tiếp tục.', 'success');
    } else {
      const token = await api.login({ email, password });
      if (version !== pageVersion) return;
      setSession({ token: token.access_token, email });
      navigate(next);
      showNotice('Đăng nhập thành công.', 'success');
    }
  } catch (error) {
    if (version === pageVersion) {
      errorElement.textContent = error.message;
      errorElement.hidden = false;
    }
  } finally {
    authBusy = false;
    button.disabled = false;
    button.textContent = register ? 'Tạo tài khoản' : 'Đăng nhập';
  }
}

async function renderOrders(version) {
  const [ordersResult, concertsResult] = await Promise.allSettled([api.orders(), api.concerts()]);
  if (version !== pageVersion) return;
  if (ordersResult.status === 'rejected') throw ordersResult.reason;
  const concerts = concertsResult.status === 'fulfilled' ? concertsResult.value : [];
  let incomplete = concertsResult.status === 'rejected';
  const groups = await Promise.allSettled(concerts.map(async (concert) => ({ concert, tickets: await api.ticketTypes(concert.id) })));
  if (version !== pageVersion) return;
  ticketLookup = new Map();
  groups.forEach((result) => {
    if (result.status === 'rejected') { incomplete = true; return; }
    result.value.tickets.forEach((ticket) => ticketLookup.set(ticket.id, { ...ticket, concert: result.value.concert }));
  });
  orderData = ordersResult.value.sort((a, b) => date(b.created_at) - date(a.created_at) || b.id - a.id);
  document.title = 'Đơn vé của tôi — Concert';
  main.innerHTML = `<div class="page-heading"><p class="eyebrow">NHỮNG CUỘC HẸN SẮP TỚI</p><h1>Đơn vé của tôi</h1><p class="lead">Xem lại và quản lý vé concert của bạn tại đây.</p></div>${incomplete ? '<div class="notice notice-info">Chưa tải được một số tên concert hoặc hạng vé. <button class="button button-link button-small" data-action="retry">Thử lại</button></div>' : ''}<div class="toolbar"><label for="order-filter">Trạng thái đơn</label><select id="order-filter"><option value="all">Tất cả đơn vé</option><option value="confirmed">Đã xác nhận</option><option value="cancelled">Đã huỷ</option></select><button class="button button-secondary button-small" data-action="retry">Làm mới</button></div><div id="order-results"></div>`;
  renderOrderList();
}

function renderOrderList() {
  const filter = document.querySelector('#order-filter').value;
  const orders = orderData.filter((order) => filter === 'all' || order.status === filter);
  document.querySelector('#order-results').innerHTML = orders.length ? `<div class="order-list">${orders.map((order) => {
    const ticket = ticketLookup.get(order.ticket_type_id);
    const confirmed = order.status === 'confirmed';
    const status = confirmed ? 'Đã xác nhận' : order.status === 'cancelled' ? 'Đã huỷ' : order.status;
    return `<article class="panel order-card"><div class="order-heading"><span class="order-number">ĐƠN #${order.id}</span><span class="badge ${confirmed ? 'badge-success' : 'badge-muted'}">${escape(status)}</span></div><h2>${escape(ticket?.concert.name || `Hạng vé #${order.ticket_type_id}`)}</h2><div class="order-details"><p><span>Hạng vé</span><strong>${escape(ticket?.name || `#${order.ticket_type_id}`)}</strong></p><p><span>Số lượng</span><strong>${order.quantity} vé</strong></p><p><span>Ngày đặt</span><strong>${escape(dateText(order.created_at))}</strong></p>${ticket ? `<p><span>Thời gian diễn</span><strong>${escape(dateText(ticket.concert.start_time))}</strong></p>` : ''}</div><div class="order-actions">${ticket ? `<a class="button button-secondary button-small" href="#/concerts/${ticket.concert.id}">Xem concert</a>` : ''}${confirmed ? `<button class="button button-danger button-small" data-action="cancel-order" data-id="${order.id}">Huỷ đơn</button>` : ''}</div></article>`;
  }).join('')}</div>` : empty(orderData.length ? 'Không có đơn ở trạng thái này' : 'Bạn chưa có đơn vé nào', 'Một đêm nhạc đáng nhớ đang chờ bạn.', '<a class="button button-primary" href="#/">Khám phá concert</a>');
}

function openConfirmation({ title, content, label, danger = false, action }) {
  if (mutationBusy) return;
  document.querySelector('#confirm-title').textContent = title;
  document.querySelector('#confirm-content').innerHTML = content;
  document.querySelector('#confirm-error').hidden = true;
  confirmButton.textContent = label;
  confirmButton.className = `button ${danger ? 'button-danger' : 'button-primary'}`;
  confirmButton.disabled = false;
  cancelButton.disabled = false;
  confirmAction = action;
  dialog.showModal();
  cancelButton.focus();
}

function bookTicket() {
  if (!detail || document.querySelector('#book-button').disabled) return;
  if (!getSession()) {
    navigate(`/login?next=${encodeURIComponent(path())}`);
    showNotice('Đăng nhập để tiếp tục đặt vé. Lựa chọn của bạn đã được giữ lại.');
    return;
  }
  const { concert } = detail;
  const ticket = detail.tickets.find((item) => item.id === detail.selectedId);
  const quantity = Number(document.querySelector('#quantity').value);
  openConfirmation({ title: 'Xác nhận đặt vé', label: 'Xác nhận đặt vé', content: `<p>${escape(concert.name)}</p><div class="summary-row"><span>${escape(ticket.name)} × ${quantity}</span><strong>${money(Number(ticket.price) * quantity)}</strong></div><p class="field-hint">Đơn sẽ được xác nhận ngay khi đặt thành công.</p>`, action: async () => {
    try {
      const order = await api.createOrder({ ticket_type_id: ticket.id, quantity });
      drafts.delete(concert.id);
      dialog.close();
      navigate('/my-orders');
      showNotice(`Đặt vé thành công! Đơn #${order.id} đã được xác nhận.`, 'success');
    } catch (error) {
      if (error.code === 'SoldOut' || error.code === 'SaleNotOpen') {
        dialog.close();
        await renderRoute();
        showNotice(error.message, 'error');
      } else if (error.code === 'NetworkError') {
        dialog.close();
        navigate('/my-orders');
        showNotice('Chưa xác định được kết quả đặt vé. Kiểm tra danh sách đơn trước khi đặt lại.', 'error');
      } else throw error;
    }
  } });
}

function cancelOrder(id) {
  const order = orderData.find((item) => item.id === id);
  if (!order || order.status !== 'confirmed') return;
  openConfirmation({ title: `Huỷ đơn #${id}?`, label: 'Xác nhận huỷ', danger: true, content: `<p>Bạn muốn huỷ đơn gồm ${order.quantity} vé? Số vé này sẽ được trả lại để người khác có thể đặt.</p>`, action: async () => {
    try {
      await api.cancelOrder(id);
      dialog.close();
      await renderRoute();
      showNotice(`Đã huỷ đơn #${id}.`, 'success');
    } catch (error) {
      if (error.code === 'InvalidState' || error.code === 'NetworkError') {
        dialog.close();
        await renderRoute();
        showNotice(error.code === 'NetworkError' ? 'Kết nối gián đoạn. Kiểm tra trạng thái đơn trước khi huỷ lại.' : error.message, 'error');
      } else throw error;
    }
  } });
}

confirmButton.addEventListener('click', async () => {
  if (mutationBusy || !confirmAction) return;
  mutationBusy = true;
  const label = confirmButton.textContent;
  confirmButton.disabled = true;
  cancelButton.disabled = true;
  confirmButton.textContent = 'Đang xử lý…';
  document.querySelector('#confirm-error').hidden = true;
  try { await confirmAction(); }
  catch (error) {
    const message = document.querySelector('#confirm-error');
    message.textContent = error.message;
    message.hidden = false;
  } finally {
    mutationBusy = false;
    confirmButton.disabled = false;
    cancelButton.disabled = false;
    confirmButton.textContent = label;
  }
});
cancelButton.addEventListener('click', () => { if (!mutationBusy) dialog.close(); });
dialog.addEventListener('cancel', (event) => { if (mutationBusy) event.preventDefault(); });

document.addEventListener('click', (event) => {
  if (event.target.closest('.skip-link')) {
    event.preventDefault();
    main.focus();
    return;
  }
  const button = event.target.closest('[data-action]');
  if (!button || button.disabled) return;
  const action = button.dataset.action;
  if (action === 'dismiss-notice') notices.innerHTML = '';
  if (action === 'retry') renderRoute();
  if (action === 'explore') {
    document.querySelector('#concert-section').scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
    document.querySelector('#search').focus({ preventScroll: true });
  }
  if (action === 'logout') {
    setSession(null);
    orderData = [];
    ticketLookup.clear();
    drafts.clear();
    navigate('/');
    showNotice('Bạn đã đăng xuất.');
  }
  if (action === 'toggle-password') {
    const input = document.querySelector('#password');
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    button.textContent = show ? 'Ẩn' : 'Hiện';
    button.setAttribute('aria-label', show ? 'Ẩn mật khẩu' : 'Hiện mật khẩu');
    button.setAttribute('aria-pressed', String(show));
  }
  if (action === 'increase' || action === 'decrease') {
    const input = document.querySelector('#quantity');
    const value = Number(input.value) || 1;
    input.value = String(Math.max(1, Math.min(Number(input.max), value + (action === 'increase' ? 1 : -1))));
    syncBooking();
  }
  if (action === 'book') bookTicket();
  if (action === 'cancel-order') cancelOrder(Number(button.dataset.id));
});

document.addEventListener('input', (event) => {
  if (event.target.id === 'search') renderConcertList();
  if (event.target.id === 'quantity') syncBooking();
});
document.addEventListener('change', (event) => {
  if (event.target.id === 'sale-filter') renderConcertList();
  if (event.target.id === 'order-filter') renderOrderList();
  if (event.target.name === 'ticket' && detail) {
    detail.selectedId = Number(event.target.value);
    const selected = detail.tickets.find((ticket) => ticket.id === detail.selectedId);
    const input = document.querySelector('#quantity');
    input.value = String(Math.max(1, Math.min(Number(input.value) || 1, Math.min(10, selected.remaining))));
    syncBooking();
  }
});
document.addEventListener('submit', (event) => {
  if (event.target.id === 'auth-form') {
    event.preventDefault();
    submitAuth(event.target);
  }
});
window.addEventListener('hashchange', renderRoute);
window.addEventListener('session-expired', () => {
  orderData = [];
  ticketLookup.clear();
  dialog.close();
  navigate(`/login?next=${encodeURIComponent(safeNext(path()))}`);
  showNotice('Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.', 'error');
});
setInterval(() => {
  if (detail) syncBooking();
  else if (path() === '/' && document.querySelector('#concert-results')) renderConcertList();
}, 30000);
renderRoute();

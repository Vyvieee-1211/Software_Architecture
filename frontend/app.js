import { api, getSession, setSession, getSavedName, saveName, newIdempotencyKey } from './api.js';
import { renderManagement } from './management.js';

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
// Lần đặt vé chưa rõ kết quả (timeout/mất mạng): giữ nguyên Idempotency-Key để lần
// đặt lại cùng hạng vé + số lượng không tạo thêm đơn.
let pendingBooking = null;
const BOOKING_RETRIES = 2;
// Virtual Waiting Room của concert đang xem: trạng thái lần hỏi gần nhất (vị trí / thẻ vào cửa).
let waitingRoom = null;
let waitingTimer = null;

// Khớp với cấu hình mặc định của server (HOLD_MINUTES, REFUND_CUTOFF_HOURS). Chỉ dùng để
// hiển thị: hạn giữ chỗ thật lấy từ `expires_at`, và server luôn là bên kiểm tra cuối cùng.
const HOLD_MINUTES = 12;
const REFUND_CUTOFF_HOURS = 24;
const ORDER_POLL_MS = 3000;
const ORDER_STATUS = {
  held: { label: 'Đang giữ chỗ', badge: 'badge-warning' },
  paid: { label: 'Đã thanh toán', badge: 'badge-success' },
  expired: { label: 'Hết hạn giữ chỗ', badge: 'badge-muted' },
  cancelled: { label: 'Đã huỷ', badge: 'badge-muted' },
  failed: { label: 'Thanh toán thất bại', badge: 'badge-danger' },
  refunded: { label: 'Đã hoàn tiền', badge: 'badge-muted' },
};
// Trang chi tiết đơn đang mở: { order, ticket, tickets, qrUrls, watching }.
let orderView = null;

const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const money = (value) => new Intl.NumberFormat('vi-VN', { style: 'currency', currency: 'VND', maximumFractionDigits: 0 }).format(Number(value));
const date = (value) => new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`);
const dateText = (value, options = {}) => !value || Number.isNaN(date(value).getTime()) ? 'Thời gian đang cập nhật' : new Intl.DateTimeFormat('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', ...options }).format(date(value));
const isOpen = (concert) => date(concert.sale_open_time).getTime() <= Date.now();
const saleBadge = (concert) => `<span class="badge ${isOpen(concert) ? 'badge-success' : 'badge-muted'}">${isOpen(concert) ? 'Đang mở bán' : 'Sắp mở bán'}</span>`;
const path = () => (location.hash.slice(1) || '/').split('?')[0];
const safeNext = (value) => /^\/(?:concerts\/\d+|my-orders|orders\/\d+|manage(?:\/\d+)?)?$/.test(value || '') ? value : '/';
const statusBadge = (status) => `<span class="badge ${ORDER_STATUS[status]?.badge || 'badge-muted'}">${escape(ORDER_STATUS[status]?.label || status)}</span>`;
const secondsLeft = (expiresAt) => Math.max(0, Math.floor((date(expiresAt).getTime() - Date.now()) / 1000));
const clockText = (seconds) => `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
const holdActive = (order) => order.status === 'held' && secondsLeft(order.expires_at) > 0;
// Hoàn tiền chỉ nhận khi còn cách giờ diễn ít nhất REFUND_CUTOFF_HOURS.
const refundOpen = (order, concert) => order.status === 'paid' && Boolean(concert)
  && date(concert.start_time).getTime() - Date.now() >= REFUND_CUTOFF_HOURS * 3600 * 1000;
const countdown = (order, size = '') => `<span class="countdown ${size}" role="timer" aria-label="Thời gian giữ chỗ còn lại"><span aria-hidden="true">⏱</span> <strong data-countdown="${escape(order.expires_at)}">${clockText(secondsLeft(order.expires_at))}</strong></span>`;
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
  const accountName = session?.full_name?.trim() || (session ? getSavedName(session.email) : '') || 'Tài khoản';
  const initials = accountName.split(/\s+/).filter(Boolean).slice(-2).map((part) => Array.from(part)[0]).join('').toLocaleUpperCase('vi-VN');
  document.querySelector('#account').classList.toggle('account-signed-in', Boolean(session));
  document.querySelector('#manage-link').hidden = !session?.is_admin;
  document.querySelector('#account').innerHTML = session
    ? `<a class="account-profile" href="#/my-orders" aria-label="${escape(accountName)} — Vé của tôi"><span class="account-avatar" aria-hidden="true">${escape(initials)}</span><span class="account-copy"><span class="account-greeting">Xin chào,</span><span class="account-name" title="${escape(accountName)}">${escape(accountName)}</span></span></a><button class="button button-secondary button-small" data-action="logout">Đăng xuất</button>`
    : '<a class="button button-secondary button-small" href="#/login">Đăng nhập</a><a class="button button-primary button-small" href="#/register">Đăng ký</a>';
  document.querySelectorAll('[data-nav]').forEach((link) => {
    const active = link.dataset.nav === 'manage' ? path().startsWith('/manage') : link.dataset.nav === 'orders' ? path() === '/my-orders' || path().startsWith('/orders/') :link.dataset.nav === 'concerts' ? path() === '/concerts' || path().startsWith('/concerts/') : path() === '/';
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
}

async function refreshProfile() {
  const session = getSession();
  if (!session) return;
  try {
    const user = await api.me();
    if (getSession()?.token !== session.token) return;
    setSession({ ...session, email: user.email, full_name: user.full_name || '', is_admin: user.is_admin });
    renderHeader();
  } catch {
    // Profile availability must not block concert browsing or ticket booking.
  }
}

function loading(message = 'Đang tải thông tin…') {
  main.innerHTML = `<div class="container state-page"><div class="loading-state" role="status"><span class="spinner" aria-hidden="true"></span><p>${escape(message)}</p></div></div>`;
}

function renderError(error) {
  main.innerHTML = `<div class="container state-page"><div class="panel error-state"><h1>Chưa thể tải thông tin</h1><p role="alert">${escape(error.message)}</p><button class="button button-primary" data-action="retry">Thử lại</button></div></div>`;
}

function empty(title, description, action = '') {
  return `<div class="panel empty-state"><span class="empty-icon" aria-hidden="true">♫</span><h2>${escape(title)}</h2><p>${escape(description)}</p>${action}</div>`;
}

async function renderRoute() {
  const version = ++pageVersion;
  detail = null;
  clearWaitingRoom();
  closeOrderView();
  if (!mutationBusy) dialog.close();
  const route = path();
  if ((route === '/my-orders' || /^\/orders\/\d+$/.test(route) || /^\/manage(?:\/\d+)?$/.test(route)) && !getSession()) {
    navigate(`/login?next=${encodeURIComponent(route)}`);
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
    else if (/^\/orders\/\d+$/.test(route)) await renderOrderDetail(version, Number(route.split('/')[2]));
    else if (/^\/manage(?:\/\d+)?$/.test(route)) await renderManagement(main, Number(route.split('/')[2]) || null, () => version === pageVersion, showNotice);
    else {
      document.title = 'Không tìm thấy trang — HIT THE VIBE';
      main.innerHTML = empty('Không tìm thấy trang', 'Đường dẫn này không tồn tại.', '<a class="button button-primary" href="#/">Về trang chủ</a>');
    }
  } catch (error) {
    if (version === pageVersion) renderError(error);
  }
  if (version === pageVersion) {
    main.focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: 'instant' });
    if (pendingDiscovery && document.querySelector('#search')) {
      pendingDiscovery = false;
      exploreDiscovery();
    }
  }
}

let discoveryQuery = '';
let discoverySale = 'all';
let discoveryVenue = '';
let discoveryCity = '';
let discoverySoon = false;
let featuredIndex = 0;
let pendingDiscovery = false;

const eventTitle = (concert) => concert.name || 'Concert đang cập nhật';
const eventArtist = (concert) => concert.artist || 'Nghệ sĩ đang cập nhật';
const eventVenue = (concert) => concert.venue || 'Địa điểm đang cập nhật';
const theme = (concert) => `poster-theme-${Math.abs(Number(concert.id) || 0) % 4}`;
const cities = [
  { id: 'hcm', name: 'TP.HCM', caption: 'Nhịp sống không ngừng', matches: ['tp hcm', 'tphcm', 'ho chi minh', 'sai gon', 'saigon'], art: 'city-hcm' },
  { id: 'hanoi', name: 'Hà Nội', caption: 'Giai điệu giữa lòng phố', matches: ['ha noi', 'hanoi'], art: 'city-hanoi' },
  { id: 'dalat', name: 'Đà Lạt', caption: 'Âm nhạc giữa những tầng mây', matches: ['da lat', 'dalat'], art: 'city-dalat' },
];
const normalize = (value) => String(value || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/đ/g, 'd').replace(/[^a-z0-9]+/g, ' ').trim();
const matchesCity = (concert, cityId) => {
  const city = cities.find((item) => item.id === cityId);
  const venue = normalize(concert.venue);
  return city ? city.matches.some((part) => venue.includes(part)) : true;
};

function poster(concert, extra = '') {
  return `<div class="event-poster ${theme(concert)} ${extra}" aria-hidden="true"><div class="poster-image"></div><div class="poster-decoration"></div><div class="poster-top"><span>HIT THE VIBE PRESENTS</span><span>LIVE<br>CONCERT</span></div><div class="poster-title">${escape(eventTitle(concert))}</div><div class="poster-artist">${escape(concert.artist || 'LIVE MUSIC EXPERIENCE')}</div><div class="poster-bottom"><span>${escape(dateText(concert.start_time, { hour: undefined, minute: undefined }))}</span><span>${escape(eventVenue(concert))}</span></div></div>`;
}

function eventCard(concert, featured = false) {
  return `<article class="concert-card ${featured ? 'spotlight-card' : ''}"><a class="poster-link" href="#/concerts/${concert.id}" tabindex="-1" aria-hidden="true">${poster(concert)}<span class="poster-cta">Mua vé ↗</span></a><div class="card-body"><div class="card-kicker">${saleBadge(concert)}<span>CONCERT</span></div><h3><a href="#/concerts/${concert.id}">${escape(eventTitle(concert))}</a></h3><p class="artist-name">${escape(eventArtist(concert))}</p><div class="card-meta"><span>◷ ${escape(dateText(concert.start_time))}</span><span>⌖ ${escape(eventVenue(concert))}</span></div><div class="card-footer"><span>${isOpen(concert) ? 'Xem hạng vé & giá vé' : `Mở bán: ${escape(dateText(concert.sale_open_time))}`}</span><a href="#/concerts/${concert.id}" aria-label="Xem vé ${escape(eventTitle(concert))}">Xem vé <span aria-hidden="true">↗</span></a></div></div></article>`;
}

function featuredConcerts() {
  const upcoming = homeConcerts.filter((concert) => date(concert.start_time).getTime() >= Date.now()).sort((a, b) => date(a.start_time) - date(b.start_time));
  return (upcoming.length ? upcoming : homeConcerts).slice(0, 4);
}

function renderFeatured() {
  const events = featuredConcerts();
  const concert = events[featuredIndex % (events.length || 1)];
  document.querySelector('#featured-hero').innerHTML = `<div class="hero-artwork ${concert ? theme(concert) : ''}" aria-hidden="true"><div class="hero-image"></div><div class="hero-flare"></div></div><div class="hero-content container"><div class="hero-overline"><span class="live-dot"></span> HIT THE VIBE / THE LIVE EXPERIENCE <span class="hero-edition">${concert ? 'FEATURED CONCERT' : 'KHÁM PHÁ CONCERT'}</span></div><div class="hero-copy"><h1 id="hero-title">${concert ? escape(eventTitle(concert)) : 'ĐÊM NAY,<br>SỐNG CÙNG<br>ÂM NHẠC.'}</h1><p class="hero-artist">${concert ? escape(eventArtist(concert)) : 'Những đêm nhạc đáng nhớ bắt đầu từ đây.'}</p>${concert ? `<div class="hero-facts"><div><span>NGÀY DIỄN</span><strong>${escape(dateText(concert.start_time))}</strong></div><div><span>ĐỊA ĐIỂM</span><strong>${escape(eventVenue(concert))}</strong></div></div>` : ''}<div class="hero-actions">${concert ? `<a class="button button-primary" href="#/concerts/${concert.id}">Mua vé ngay <span aria-hidden="true">↗</span></a>${saleBadge(concert)}` : '<button class="button button-primary" data-action="explore">Khám phá concert ↗</button>'}</div></div><div class="hero-bottom"><span class="hero-signature">FEEL IT.<br><em>LIVE IT.</em></span><div class="hero-pagination" aria-label="Chọn concert nổi bật">${events.map((item, index) => `<button data-action="feature-event" data-index="${index}" aria-label="${escape(eventTitle(item))}" aria-pressed="${index === featuredIndex}"><span>${String(index + 1).padStart(2, '0')}</span><span class="hero-page-title">${escape(eventTitle(item))}</span></button>`).join('')}</div></div></div>`;
}

async function renderHome(version) {
  const concerts = await api.concerts();
  if (version !== pageVersion) return;
  homeConcerts = concerts;
  featuredIndex = 0;
  const selected = featuredConcerts();
  const selling = concerts.filter(isOpen).slice(0, 5);
  const venues = [...new Set(concerts.map((concert) => concert.venue).filter(Boolean))];
  document.title = 'HIT THE VIBE — Concert & vé sự kiện';
  main.innerHTML = `<section class="featured-hero" id="featured-hero" aria-labelledby="hero-title"></section>
    <div class="vibe-ticker" aria-hidden="true"><span>LIVE MUSIC</span><span>✳</span><span>GOOD ENERGY</span><span>✳</span><span>UNFORGETTABLE NIGHTS</span><span>✳</span><span>HIT THE VIBE</span><span>✳</span><span>LIVE MUSIC</span></div>
    <section class="spotlight-section container" aria-labelledby="featured-title"><div class="section-heading"><div><p class="eyebrow">01 / THE SPOTLIGHT</p><h2 id="featured-title">Sự kiện <em>nổi bật.</em></h2></div><div class="section-tools"><span>Đêm nhạc tiếp theo của bạn</span><div class="rail-controls"><button data-action="rail-prev" aria-label="Sự kiện trước">←</button><button data-action="rail-next" aria-label="Sự kiện tiếp theo">→</button></div></div></div><div class="spotlight-rail" id="featured-rail" tabindex="0" aria-label="Sự kiện nổi bật">${selected.length ? selected.map((concert) => eventCard(concert, true)).join('') : empty('Lịch diễn đang được cập nhật', 'Hẹn bạn ở những đêm nhạc tiếp theo.')}</div></section>
    <section class="on-sale-section" aria-labelledby="on-sale-title"><div class="container on-sale-layout"><div class="on-sale-intro"><p class="eyebrow">02 / TICKETS ARE LIVE</p><h2 id="on-sale-title">Đang<br><em>mở bán</em><span>↗</span></h2><p>Tấm vé đến gần hơn.<br>Chọn đêm nhạc của riêng bạn.</p><button class="button button-white" data-action="discover-open">Khám phá vé ↗</button></div><div class="sale-event-list">${selling.length ? selling.map((concert, index) => `<article class="sale-event"><a class="sale-mini-poster" href="#/concerts/${concert.id}" tabindex="-1" aria-hidden="true">${poster(concert)}</a><div class="sale-event-copy"><span class="sale-index">${String(index + 1).padStart(2, '0')} / LIVE CONCERT</span><h3><a href="#/concerts/${concert.id}">${escape(eventTitle(concert))}</a></h3><p>${escape(eventArtist(concert))}</p><div>${escape(dateText(concert.start_time))}<span> · </span>${escape(eventVenue(concert))}</div></div><a class="sale-ticket-link" href="#/concerts/${concert.id}" aria-label="Mua vé ${escape(eventTitle(concert))}">Mua vé <span>↗</span></a></article>`).join('') : '<div class="dark-empty"><h3>Chưa có sự kiện đang mở bán</h3><p>Khám phá lịch diễn và thời gian mở bán trong danh sách concert.</p><button class="button button-white" data-action="explore">Xem concert ↗</button></div>'}</div></div></section>
    <section class="discovery-section container" id="concert-section" aria-labelledby="concerts-title"><div class="section-heading"><div><p class="eyebrow">03 / FIND YOUR NEXT NIGHT OUT</p><h2 id="concerts-title">Tìm đúng <em>vibe của bạn.</em></h2></div><span class="result-count" id="result-count" aria-live="polite"></span></div><form class="discovery-search" id="discovery-form" role="search"><label class="sr-only" for="search">Tìm concert, nghệ sĩ hoặc địa điểm</label><span aria-hidden="true">⌕</span><input id="search" type="search" value="${escape(discoveryQuery)}" placeholder="Bạn muốn xem concert nào?" autocomplete="off"><button class="button button-primary" type="submit">Tìm sự kiện ↗</button></form><div class="discovery-filters"><div class="filter-chips" aria-label="Bộ lọc sự kiện"><button class="chip" data-action="all-chip">Tất cả</button>${cities.map((city) => `<button class="chip" data-action="city-chip" data-city="${city.id}">${city.name}</button>`).join('')}<button class="chip" data-action="soon-chip">Sắp diễn ra</button><button class="chip" data-action="sale-chip" data-value="open">Đang mở bán</button><button class="chip" data-action="sale-chip" data-value="upcoming">Sắp mở bán</button></div><div class="filter-selects"><label class="sr-only" for="sale-filter">Trạng thái mở bán</label><select id="sale-filter"><option value="all">Tất cả trạng thái</option><option value="open">Đang mở bán</option><option value="upcoming">Sắp mở bán</option></select><label class="sr-only" for="venue-filter">Địa điểm</label><select id="venue-filter"><option value="">Tất cả địa điểm</option>${venues.map((venue) => `<option value="${escape(venue)}">${escape(venue)}</option>`).join('')}</select></div></div><div id="concert-results"></div></section>
    <section class="locations-section container" aria-labelledby="locations-title"><div class="section-heading"><div><p class="eyebrow">04 / MUSIC IS EVERYWHERE</p><h2 id="locations-title">Khám phá theo <em>địa điểm.</em></h2></div><p>Thành phố của bạn. Giai điệu của bạn.</p></div><div class="location-grid">${cities.map((city, index) => `<button class="location-tile ${city.art}" data-action="location-discovery" data-city="${city.id}"><span class="city-art" aria-hidden="true"></span><span class="location-number">0${index + 1} / VIỆT NAM</span><span class="location-copy"><strong>${city.name}</strong><span>${city.caption}</span><small>${concerts.filter((concert) => matchesCity(concert, city.id)).length} sự kiện có địa điểm phù hợp</small></span><span class="location-arrow" aria-hidden="true">↗</span></button>`).join('')}</div></section>
    <section class="promo-section"><div class="promo-image" aria-hidden="true"></div><div class="container promo-content"><p class="eyebrow">DON'T JUST LISTEN. BE THERE.</p><h2>CÓ NHỮNG ĐÊM<br><em>KHÔNG THỂ BỎ LỠ.</em></h2><div><p>Đứng giữa đám đông. Hát cùng giai điệu yêu thích.<br>Giữ lại những khoảnh khắc thuộc về bạn.</p><button class="button button-primary" data-action="explore">Tìm đêm nhạc tiếp theo ↗</button></div></div></section>`;
  document.querySelector('#sale-filter').value = discoverySale;
  document.querySelector('#venue-filter').value = venues.includes(discoveryVenue) ? discoveryVenue : '';
  document.querySelector('#nav-search').value = discoveryQuery;
  renderFeatured();
  renderConcertList();
}

function renderConcertList() {
  const input = document.querySelector('#search');
  if (!input) return;
  discoveryQuery = input.value;
  const query = discoveryQuery.trim().toLocaleLowerCase('vi');
  discoverySale = document.querySelector('#sale-filter').value;
  discoveryVenue = document.querySelector('#venue-filter').value;
  let concerts = homeConcerts.filter((concert) => `${eventTitle(concert)} ${concert.artist || ''} ${concert.venue || ''}`.toLocaleLowerCase('vi').includes(query) && (discoverySale === 'all' || isOpen(concert) === (discoverySale === 'open')) && (!discoveryVenue || concert.venue === discoveryVenue) && matchesCity(concert, discoveryCity) && (!discoverySoon || date(concert.start_time).getTime() >= Date.now()));
  if (discoverySoon) concerts = concerts.sort((a, b) => date(a.start_time) - date(b.start_time));
  document.querySelector('#result-count').textContent = `${concerts.length} sự kiện`;
  document.querySelectorAll('.filter-chips .chip').forEach((button) => {
    const active = button.dataset.action === 'all-chip' ? !discoveryCity && !discoverySoon && discoverySale === 'all' && !discoveryVenue : button.dataset.action === 'city-chip' ? button.dataset.city === discoveryCity : button.dataset.action === 'soon-chip' ? discoverySoon : button.dataset.value === discoverySale;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  document.querySelector('#concert-results').innerHTML = concerts.length ? `<div class="concert-grid">${concerts.map((concert) => eventCard(concert)).join('')}</div>` : empty('Chưa tìm thấy sự kiện', homeConcerts.length ? 'Thử địa điểm khác hoặc thay đổi bộ lọc. Địa điểm được tìm theo thông tin concert hiện có.' : 'Lịch diễn mới sẽ được cập nhật tại đây.', homeConcerts.length ? '<button class="button button-primary" data-action="reset-discovery">Xóa bộ lọc</button>' : '');
}

function exploreDiscovery() {
  document.querySelector('#concert-section')?.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
  document.querySelector('#search')?.focus({ preventScroll: true });
}

function rowQuantity(ticket, selectedId, quantity) {
  const selected = ticket.id === selectedId;
  return `<div class="row-quantity"><button type="button" data-action="ticket-decrease" data-id="${ticket.id}" aria-label="Giảm số lượng ${escape(ticket.name)}" ${!selected || quantity <= 1 || ticket.remaining <= 0 ? 'disabled' : ''}>−</button><input type="number" id="ticket-quantity-${ticket.id}" data-ticket-quantity="${ticket.id}" min="${selected ? 1 : 0}" max="${Math.min(10, ticket.remaining)}" step="1" value="${selected ? quantity : 0}" inputmode="numeric" aria-label="Số lượng ${escape(ticket.name)}" ${ticket.remaining <= 0 ? 'disabled' : ''}><button type="button" data-action="ticket-increase" data-id="${ticket.id}" aria-label="Tăng số lượng ${escape(ticket.name)}" ${ticket.remaining <= 0 || selected && quantity >= Math.min(10, ticket.remaining) ? 'disabled' : ''}>+</button></div>`;
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
  const quantity = Math.min(Math.max(1, draft?.quantity || 1), Math.min(10, selected?.remaining || 1));
  detail = { concert, tickets, selectedId: selected?.id };
  document.title = `${eventTitle(concert)} — HIT THE VIBE`;
  main.innerHTML = `<section class="detail-art-banner"><div class="container"><a class="back-link" href="#/concerts">← Tất cả concert</a><div class="detail-banner-title"><p class="eyebrow">HIT THE VIBE / LIVE CONCERT</p><h1>${escape(eventTitle(concert))}</h1><span>${escape(eventArtist(concert))}</span></div><span class="detail-banner-stamp" aria-hidden="true">LIVE<br>EXPERIENCE ↗</span></div></section>
    <div class="container detail-page"><div class="detail-grid"><div class="event-information"><section class="detail-heading"><p class="eyebrow">THÔNG TIN SỰ KIỆN</p>${saleBadge(concert)}<h2>${escape(eventTitle(concert))}</h2><div class="event-info-grid"><div><span>Nghệ sĩ</span><strong>${escape(eventArtist(concert))}</strong></div><div><span>Thời gian</span><strong>${escape(dateText(concert.start_time))}</strong></div><div><span>Địa điểm</span><strong>${escape(eventVenue(concert))}</strong></div><div><span>Thời gian mở bán</span><strong>${escape(dateText(concert.sale_open_time))}</strong></div></div></section><section class="event-about"><h2>Về sự kiện</h2><p>Thông tin giới thiệu chi tiết sẽ được cập nhật. Bạn có thể xem nghệ sĩ, thời gian và địa điểm diễn ở trên, chọn hạng vé và xác nhận đơn vé tại đây.</p>${poster(concert, 'detail-poster')}<p class="artwork-caption">HIT THE VIBE / Minh họa không khí đêm nhạc.</p></section><section class="booking-guide"><p class="eyebrow">TRƯỚC KHI ĐẶT VÉ</p><h2>Sẵn sàng cho đêm nhạc.</h2><ol><li>Chọn một hạng vé và số lượng phù hợp.</li><li>Kiểm tra tổng tiền trước khi xác nhận.</li><li>Quản lý vé đã đặt trong mục “Vé của tôi”.</li></ol></section></div>
      <aside class="booking-panel" aria-labelledby="booking-title"><div class="booking-panel-header"><span class="eyebrow">YOUR TICKET TO THE MOMENT</span><h2 id="booking-title">Chọn vé của bạn</h2><p>Tối đa 10 vé mỗi lần · Giữ chỗ 12 phút để thanh toán</p></div><div id="sale-notice">${!isOpen(concert) ? `<div class="notice notice-info">Vé mở bán lúc ${escape(dateText(concert.sale_open_time))} (giờ Việt Nam).</div>` : ''}</div><div id="waiting-room" aria-live="polite"></div><div class="ticket-list" role="radiogroup" aria-label="Chọn hạng vé">${tickets.length ? tickets.map((ticket) => `<div class="ticket-option ${selected?.id === ticket.id ? 'is-selected' : ''}"><label class="ticket-option-label"><input type="radio" name="ticket" value="${ticket.id}" ${selected?.id === ticket.id ? 'checked' : ''} ${ticket.remaining <= 0 ? 'disabled' : ''}><span class="ticket-copy"><strong>${escape(ticket.name)}</strong><span class="ticket-stock">${ticket.remaining > 0 ? `Còn ${ticket.remaining.toLocaleString('vi-VN')} vé` : 'Đã hết vé'}</span></span></label><div class="ticket-option-bottom"><span class="ticket-price">${money(ticket.price)}</span>${rowQuantity(ticket, selected?.id, quantity)}</div></div>`).join('') : empty('Chưa có hạng vé', 'Thông tin vé sẽ được cập nhật sau.')}</div><div class="booking-summary"><div class="summary-row"><span>Hạng vé</span><strong id="summary-ticket"></strong></div><div class="summary-row"><span>Đơn giá</span><strong id="summary-price"></strong></div><div class="selected-quantity"><label for="quantity">Số lượng đã chọn</label><div class="quantity-control"><button type="button" data-action="decrease" aria-label="Giảm số lượng">−</button><input id="quantity" type="number" min="1" max="10" step="1" value="${quantity}" inputmode="numeric" aria-describedby="quantity-error"><button type="button" data-action="increase" aria-label="Tăng số lượng">+</button></div></div><p class="form-error" id="quantity-error" hidden></p><div class="summary-total"><span>Tổng tiền</span><strong id="summary-total"></strong></div><button id="book-button" class="button button-primary button-full" data-action="book" type="button"></button><p class="form-error" id="book-error" role="alert" hidden></p><p class="booking-footnote">Vé được giữ cho bạn 12 phút trong lúc thanh toán. Quá thời gian, vé tự động được trả lại kho.</p></div></aside>
    </div></div>`;
  syncBooking();
  if (getSession()) syncWaitingRoom(id, version);
}

// --- Virtual Waiting Room ------------------------------------------------------
// Trong đợt mở bán, server chỉ nhận đặt vé từ người đã tới lượt (có thẻ vào cửa).
// Trang tự vào hàng, hỏi lại vị trí theo nhịp server gợi ý, và mở nút đặt vé khi tới lượt.

function clearWaitingRoom() {
  clearTimeout(waitingTimer);
  waitingTimer = null;
  waitingRoom = null;
}

const waitingRoomBlocks = () => Boolean(waitingRoom && waitingRoom.concertId === detail?.concert.id
  && !['inactive', 'admitted'].includes(waitingRoom.status));

async function syncWaitingRoom(concertId, version) {
  clearTimeout(waitingTimer);
  let state;
  try {
    state = await api.joinWaitingRoom(concertId);
  } catch (error) {
    // Mất mạng, quá tải hoặc bị giới hạn tốc độ: giữ trạng thái đang có và hỏi lại sau.
    if (version === pageVersion) waitingTimer = setTimeout(() => syncWaitingRoom(concertId, version), 5000);
    return;
  }
  if (version !== pageVersion || detail?.concert.id !== concertId) return;
  waitingRoom = { concertId, ...state };
  renderWaitingRoom();
  syncBooking();
  if (state.status === 'waiting' || state.status === 'not_open') {
    const seconds = state.status === 'waiting'
      ? state.estimated_wait_seconds / 4
      : (date(state.opens_at).getTime() - Date.now()) / 1000;
    waitingTimer = setTimeout(() => syncWaitingRoom(concertId, version), Math.min(15, Math.max(2, seconds || 2)) * 1000);
  } else if (state.status === 'admitted') {
    // Đếm ngược thời hạn thẻ vào cửa; hết hạn thì khóa nút đặt vé và mời xếp hàng lại.
    const tick = () => {
      if (version !== pageVersion) return;
      if (date(waitingRoom.pass_expires_at).getTime() <= Date.now()) {
        waitingRoom.status = 'expired';
        renderWaitingRoom();
        syncBooking();
        return;
      }
      renderWaitingRoom();
      waitingTimer = setTimeout(tick, 1000);
    };
    tick();
  }
}

function renderWaitingRoom() {
  const box = document.querySelector('#waiting-room');
  if (!box) return;
  const state = waitingRoom;
  if (!state || state.status === 'inactive' || state.status === 'not_joined') {
    box.innerHTML = '';
    return;
  }
  if (state.status === 'not_open') {
    box.innerHTML = `<div class="notice notice-info">Hàng chờ mua vé mở lúc ${escape(dateText(state.opens_at))}. Hãy giữ trang này mở, bạn sẽ được xếp hàng tự động.</div>`;
  } else if (state.status === 'waiting') {
    const minutes = Math.ceil((state.estimated_wait_seconds || 0) / 60);
    const wait = minutes <= 1 ? 'dưới 1 phút' : `khoảng ${minutes.toLocaleString('vi-VN')} phút`;
    box.innerHTML = `<div class="notice notice-info"><span><strong>Bạn đang trong hàng chờ · vị trí ${Number(state.position).toLocaleString('vi-VN')}</strong><br>Ước tính còn ${wait}. Giữ trang này mở, trang sẽ tự cập nhật; tải lại trang không làm mất chỗ.</span></div>`;
  } else if (state.status === 'admitted') {
    const left = Math.max(0, Math.floor((date(state.pass_expires_at).getTime() - Date.now()) / 1000));
    const clock = `${String(Math.floor(left / 60)).padStart(2, '0')}:${String(left % 60).padStart(2, '0')}`;
    box.innerHTML = `<div class="notice notice-success"><span><strong>Đến lượt bạn!</strong> Còn ${clock} để đặt vé.</span></div>`;
  } else if (state.status === 'expired') {
    box.innerHTML = '<div class="notice notice-error"><span>Lượt đặt vé của bạn đã hết hạn.</span><button class="button button-secondary button-small" data-action="rejoin-waiting-room" type="button">Xếp hàng lại</button></div>';
  }
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
  const queued = waitingRoomBlocks();
  const queueText = { waiting: `Đang xếp hàng · vị trí ${waitingRoom?.position ?? ''}`, not_open: 'Chờ mở hàng chờ', expired: 'Hết lượt · hãy xếp hàng lại' }[waitingRoom?.status] || 'Đang vào hàng chờ…';
  button.disabled = !valid || !open || !ticket || queued;
  button.textContent = !open ? 'Chưa đến thời gian mở bán' : !ticket ? 'Chưa có vé khả dụng' : !getSession() ? 'Đăng nhập để đặt vé ↗' : queued ? queueText : 'Đặt vé & thanh toán ↗';
  document.querySelector('[data-action="decrease"]').disabled = !ticket || quantity <= 1;
  document.querySelector('[data-action="increase"]').disabled = !ticket || quantity >= max;
  document.querySelectorAll('.ticket-option').forEach((row) => {
    const radio = row.querySelector('input[name="ticket"]');
    const selected = Number(radio.value) === detail.selectedId;
    row.classList.toggle('is-selected', selected);
    radio.checked = selected;
    const rowInput = row.querySelector('[data-ticket-quantity]');
    if (document.activeElement !== rowInput) rowInput.value = selected ? input.value : '0';
    rowInput.min = selected ? '1' : '0';
    rowInput.setAttribute('aria-invalid', String(selected && !valid));
    row.querySelector('[data-action="ticket-decrease"]').disabled = !selected || quantity <= 1;
    row.querySelector('[data-action="ticket-increase"]').disabled = rowInput.disabled || selected && quantity >= max;
  });
  if (open) document.querySelector('#sale-notice').innerHTML = '';
  if (valid) drafts.set(detail.concert.id, { ticketId: ticket.id, quantity });
}
function renderAuth(register) {
  document.title = `${register ? 'Đăng ký' : 'Đăng nhập'} — HIT THE VIBE`;
  const next = nextPage();
  main.innerHTML = `<div class="auth-page"><div class="auth-card"><a class="auth-back" href="#/">← Trở lại khám phá concert</a><div class="auth-concert-strip" aria-hidden="true"><span>HIT THE VIBE</span><span>LIVE MUSIC. REAL MOMENTS. ↗</span></div><p class="eyebrow">TÀI KHOẢN CỦA BẠN</p><h1>${register ? 'Tạo tài khoản' : 'Chào mừng trở lại.'}</h1><p class="auth-subtitle">${register ? 'Đặt vé và quản lý những đêm nhạc bạn yêu thích.' : 'Đăng nhập để tiếp tục đặt vé và xem đơn vé của bạn.'}</p><form id="auth-form" data-mode="${register ? 'register' : 'login'}">    ${register ? '<div class="field"><label for="full-name">Họ và tên <span class="optional">(không bắt buộc)</span></label><input id="full-name" name="full_name" autocomplete="name" maxlength="255" placeholder="Nguyễn Văn A"></div>' : ''}
    <div class="field"><label for="email">Email</label><input id="email" name="email" type="email" autocomplete="email" required placeholder="ban@example.com" maxlength="255"></div>
    <div class="field"><label for="password">Mật khẩu</label><div class="password-field"><input id="password" name="password" type="password" autocomplete="${register ? 'new-password' : 'current-password'}" ${register ? 'minlength="6" maxlength="72"' : 'maxlength="128"'} required placeholder="${register ? 'Ít nhất 6 ký tự' : 'Nhập mật khẩu'}"><button type="button" class="show-password" data-action="toggle-password" aria-controls="password" aria-label="Hiện mật khẩu" aria-pressed="false">Hiện</button></div></div>
    ${register ? '<div class="field"><label for="confirm-password">Xác nhận mật khẩu</label><input id="confirm-password" name="confirm_password" type="password" autocomplete="new-password" required placeholder="Nhập lại mật khẩu"></div>' : ''}
    <p class="form-error" id="auth-error" role="alert" hidden></p><button class="button button-primary button-full" id="auth-submit" type="submit">${register ? 'Tạo tài khoản' : 'Đăng nhập'}</button></form><p class="auth-switch">${register ? 'Đã có tài khoản?' : 'Chưa có tài khoản?'} <a href="#/${register ? 'login' : 'register'}?next=${encodeURIComponent(next)}">${register ? 'Đăng nhập' : 'Đăng ký ngay'}</a></p></div></div>`;
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
      const fullName = values.get('full_name').trim();
      const user = await api.register({ email, password, full_name: fullName || null });
      saveName(email, user?.full_name || fullName);
      if (version !== pageVersion) return;
      navigate(`/login?next=${encodeURIComponent(next)}`);
      showNotice('Tạo tài khoản thành công. Hãy đăng nhập để tiếp tục.', 'success');
    } else {
      const token = await api.login({ email, password });
      if (version !== pageVersion) return;
      setSession({ token: token.access_token, email, full_name: getSavedName(email) });
      refreshProfile();
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

// Bảng tra hạng vé → { ...hạng vé, concert } để hiện tên concert, giờ diễn cho từng đơn.
async function loadTicketLookup() {
  let incomplete = false;
  let concerts = [];
  try { concerts = await api.concerts(); } catch { incomplete = true; }
  const groups = await Promise.allSettled(concerts.map(async (concert) => ({ concert, tickets: await api.ticketTypes(concert.id) })));
  const lookup = new Map();
  groups.forEach((result) => {
    if (result.status === 'rejected') { incomplete = true; return; }
    result.value.tickets.forEach((ticket) => lookup.set(ticket.id, { ...ticket, concert: result.value.concert }));
  });
  return { lookup, incomplete };
}

async function renderOrders(version) {
  const [orders, { lookup, incomplete }] = await Promise.all([api.orders(), loadTicketLookup()]);
  if (version !== pageVersion) return;
  ticketLookup = lookup;
  orderData = orders.sort((a, b) => date(b.created_at) - date(a.created_at) || b.id - a.id);
  const options = Object.entries(ORDER_STATUS).map(([value, { label }]) => `<option value="${value}">${escape(label)}</option>`).join('');
  document.title = 'Đơn vé của tôi — HIT THE VIBE';
  main.innerHTML = `<div class="container orders-page"><div class="page-heading"><p class="eyebrow">NHỮNG CUỘC HẸN SẮP TỚI</p><h1>Đơn vé của tôi</h1><p class="lead">Thanh toán đơn đang giữ chỗ, xem vé kèm mã QR hoặc huỷ đơn tại đây.</p></div>${incomplete ? '<div class="notice notice-info">Chưa tải được một số tên concert hoặc hạng vé. <button class="button button-link button-small" data-action="retry">Thử lại</button></div>' : ''}<div class="toolbar"><label for="order-filter">Trạng thái đơn</label><select id="order-filter"><option value="all">Tất cả đơn vé</option>${options}</select><button class="button button-secondary button-small" data-action="retry">Làm mới</button></div><div id="order-results"></div></div>`;
  renderOrderList();
}

// Các nút thao tác theo trạng thái đơn: giữ chỗ → thanh toán / huỷ giữ chỗ; đã thanh toán → xem vé / huỷ & hoàn tiền.
function orderActions(order, concert, onDetail = false) {
  const size = onDetail ? '' : ' button-small';
  const actions = [];
  if (!onDetail) actions.push(`<a class="button button-secondary${size}" href="#/orders/${order.id}">${order.status === 'paid' ? 'Xem vé QR' : 'Chi tiết đơn'}</a>`);
  if (order.status === 'held') {
    actions.push(`<button class="button button-danger${size}" data-action="cancel-order" data-id="${order.id}">Huỷ giữ chỗ</button>`);
    actions.push(`<button class="button button-primary${size}" data-action="pay-order" data-id="${order.id}" data-expires="${escape(order.expires_at)}" ${holdActive(order) ? '' : 'disabled'}>Thanh toán ${money(order.total_amount)}</button>`);
  }
  if (order.status === 'paid' && refundOpen(order, concert)) {
    actions.push(`<button class="button button-danger${size}" data-action="cancel-order" data-id="${order.id}">Huỷ vé &amp; hoàn tiền</button>`);
  }
  return actions.join('');
}

function renderOrderList() {
  const filter = document.querySelector('#order-filter').value;
  const orders = orderData.filter((order) => filter === 'all' || order.status === filter);
  document.querySelector('#order-results').innerHTML = orders.length ? `<div class="order-list">${orders.map((order) => {
    const ticket = ticketLookup.get(order.ticket_type_id);
    const hold = order.status === 'held'
      ? `<div class="order-hold">${countdown(order)}<span>Hoàn tất thanh toán trước khi hết giờ giữ chỗ.</span></div>`
      : '';
    return `<article class="panel order-card order-card-${escape(order.status)}"><div class="order-heading"><span class="order-number">ĐƠN #${order.id}</span>${statusBadge(order.status)}</div><h2><a class="order-title-link" href="#/orders/${order.id}">${escape(ticket?.concert.name || `Hạng vé #${order.ticket_type_id}`)}</a></h2>${hold}<div class="order-details"><p><span>Hạng vé</span><strong>${escape(ticket?.name || `#${order.ticket_type_id}`)}</strong></p><p><span>Số lượng</span><strong>${order.quantity} vé</strong></p><p><span>Tổng tiền</span><strong>${money(order.total_amount)}</strong></p><p><span>Ngày giữ chỗ</span><strong>${escape(dateText(order.created_at))}</strong></p>${ticket ? `<p><span>Thời gian diễn</span><strong>${escape(dateText(ticket.concert.start_time))}</strong></p>` : ''}</div><div class="order-actions">${orderActions(order, ticket?.concert)}</div></article>`;
  }).join('')}</div>` : empty(orderData.length ? 'Không có đơn ở trạng thái này' : 'Bạn chưa có đơn vé nào', 'Một đêm nhạc đáng nhớ đang chờ bạn.', '<a class="button button-primary" href="#/">Khám phá concert</a>');
}

// --- Trang chi tiết đơn: Chọn vé → Giữ chỗ (12 phút) → Thanh toán → Xuất vé QR / Hoàn kho ---

function closeOrderView() {
  orderView?.qrUrls.forEach((url) => URL.revokeObjectURL(url));
  orderView = null;
}

async function renderOrderDetail(version, id) {
  const [order, { lookup }] = await Promise.all([api.order(id), loadTicketLookup()]);
  if (version !== pageVersion) return;
  orderView = { order, ticket: lookup.get(order.ticket_type_id) || null, tickets: null, qrUrls: [], watching: false, expiredShown: false, version };
  paintOrderView();
  if (order.status === 'paid') loadOrderTickets(version);
  // Đơn còn giữ chỗ (vừa quay về từ cổng mà webhook chưa tới, hoặc hết giờ mà server chưa kịp
  // chuyển `expired`): theo dõi đến khi đơn đổi trạng thái.
  if (order.status === 'held') watchOrder(version);
}

function orderSteps(order) {
  const outcome = {
    paid: ['Đã xuất vé QR', 'done'],
    failed: ['Đã hoàn kho', 'stopped'],
    expired: ['Đã hoàn kho', 'stopped'],
    cancelled: ['Đã hoàn kho', 'stopped'],
    refunded: ['Đã hoàn tiền & hoàn kho', 'stopped'],
  }[order.status];
  const paymentState = order.status === 'held' ? 'current'
    : order.status === 'paid' || order.status === 'refunded' ? 'done'
      : order.status === 'failed' ? 'stopped' : 'todo';
  const steps = [
    ['Chọn vé', `${order.quantity} vé · tối đa 10`, 'done'],
    ['Giữ chỗ', `Khoá ${HOLD_MINUTES} phút`, 'done'],
    ['Thanh toán', 'Cổng thanh toán bên thứ 3', paymentState],
    ['Xuất vé / Hoàn kho', outcome ? outcome[0] : 'Chờ kết quả thanh toán', outcome ? outcome[1] : 'todo'],
  ];
  return `<ol class="order-steps" aria-label="Tiến trình đơn vé">${steps.map(([title, note, state], index) => `<li class="order-step step-${state}" ${state === 'current' ? 'aria-current="step"' : ''}><span class="step-index" aria-hidden="true">${state === 'done' ? '✓' : state === 'stopped' ? '×' : index + 1}</span><span class="step-copy"><strong>${escape(title)}</strong><span>${escape(note)}</span></span></li>`).join('')}</ol>`;
}

function orderPanel(order, concert) {
  const total = money(order.total_amount);
  const again = concert ? `<a class="button button-secondary" href="#/concerts/${concert.id}">Đặt lại vé</a>` : '';
  if (order.status === 'held') {
    if (!holdActive(order)) {
      return `<p class="eyebrow">GIỮ CHỖ</p><h2>Hết thời gian giữ chỗ</h2><p>Vé đang được trả lại kho. Trang sẽ tự cập nhật trong giây lát.</p>`;
    }
    return `<p class="eyebrow">VÉ ĐANG ĐƯỢC GIỮ CHO BẠN</p>${countdown(order, 'countdown-large')}<p>Số vé này đã được trừ tạm khỏi kho và khoá riêng cho bạn. Hoàn tất thanh toán trước khi hết giờ; quá hạn, vé sẽ tự động được trả lại kho. Nếu bạn vừa thanh toán xong, trang sẽ tự cập nhật khi cổng thanh toán báo kết quả.</p><div class="order-panel-actions">${orderActions(order, concert, true)}</div>`;
  }
  if (order.status === 'paid') {
    const refund = refundOpen(order, concert)
      ? `<div class="order-panel-actions">${orderActions(order, concert, true)}</div><p class="field-hint">Huỷ vé được hoàn toàn bộ tiền nếu còn cách giờ diễn ít nhất ${REFUND_CUTOFF_HOURS} giờ.</p>`
      : `<p class="field-hint">Đã quá hạn huỷ vé: chỉ huỷ và hoàn tiền được trước giờ diễn ít nhất ${REFUND_CUTOFF_HOURS} giờ.</p>`;
    return `<p class="eyebrow">ĐÃ CHỐT ĐƠN</p><h2>Thanh toán thành công</h2><p>Đã thanh toán <strong>${total}</strong> lúc ${escape(dateText(order.paid_at))}. Vé và mã QR ở bên dưới — xuất trình mã QR khi vào cổng.</p>${refund}`;
  }
  const outcomes = {
    failed: ['Thanh toán không thành công', 'Cổng thanh toán báo giao dịch không thành công. Vé đã được hoàn về kho và bạn không bị trừ tiền.'],
    expired: ['Hết thời gian giữ chỗ', `Đơn đã quá ${HOLD_MINUTES} phút mà chưa thanh toán nên vé đã được trả lại kho.`],
    cancelled: ['Đã huỷ giữ chỗ', 'Bạn đã huỷ đơn khi chưa thanh toán. Vé đã được trả lại kho. Nếu bạn đã lỡ trả tiền ở cổng thanh toán, hệ thống sẽ tự động hoàn lại.'],
    refunded: ['Đã huỷ vé và hoàn tiền', `${total} được hoàn về phương thức thanh toán ban đầu. Mã QR của các vé đã bị vô hiệu và vé đã được trả lại kho.`],
  };
  const [title, text] = outcomes[order.status] || ['Trạng thái đơn', order.status];
  return `<p class="eyebrow">KẾT QUẢ</p><h2>${escape(title)}</h2><p>${escape(text)}</p><div class="order-panel-actions">${again}</div>`;
}

function ticketCards() {
  const { tickets } = orderView;
  if (tickets === null) return '<div class="loading-state" role="status"><span class="spinner" aria-hidden="true"></span><p>Đang tải vé…</p></div>';
  if (tickets instanceof Error) return `<div class="notice notice-error">${escape(tickets.message)} <button class="button button-link button-small" data-action="reload-tickets">Thử lại</button></div>`;
  if (!tickets.length) return empty('Chưa có vé', 'Vé của đơn này chưa được xuất.');
  return `<div class="ticket-grid">${tickets.map((ticket, index) => `<article class="ticket-card"><div class="ticket-card-qr">${ticket.qrUrl ? `<img src="${ticket.qrUrl}" alt="Mã QR vé ghế ${escape(ticket.seat_no)}" width="168" height="168">` : '<span>Chưa tải được mã QR</span>'}</div><div class="ticket-card-info"><span class="order-number">VÉ ${index + 1}/${tickets.length}</span><span>Số ghế</span><strong>${escape(ticket.seat_no)}</strong><code title="Nội dung mã QR">${escape(ticket.qr_code || '')}</code></div></article>`).join('')}</div>`;
}

function paintOrderView() {
  if (!orderView) return;
  const { order, ticket } = orderView;
  const concert = ticket?.concert;
  document.title = `Đơn #${order.id} — HIT THE VIBE`;
  const details = [
    ['Hạng vé', ticket?.name || `#${order.ticket_type_id}`],
    ['Số lượng', `${order.quantity} vé`],
    ['Đơn giá', money(order.unit_price)],
    ['Tổng tiền', money(order.total_amount)],
    ['Thời điểm giữ chỗ', dateText(order.created_at)],
    ...(order.status === 'held' ? [['Giữ chỗ đến', dateText(order.expires_at, { second: '2-digit' })]] : []),
    ...(concert ? [['Thời gian diễn', dateText(concert.start_time)], ['Địa điểm', eventVenue(concert)]] : []),
  ];
  main.innerHTML = `<div class="container order-page"><a class="order-back" href="#/my-orders">← Vé của tôi</a><div class="page-heading order-page-heading"><p class="eyebrow">ĐƠN #${order.id}</p><h1>${escape(concert?.name || `Hạng vé #${order.ticket_type_id}`)}</h1>${statusBadge(order.status)}</div>${orderSteps(order)}<div class="order-page-grid"><section class="panel order-summary" aria-labelledby="order-summary-title"><h2 id="order-summary-title">Chi tiết đơn</h2><div class="order-details">${details.map(([label, value]) => `<p><span>${escape(label)}</span><strong>${escape(value)}</strong></p>`).join('')}</div></section><section class="panel order-panel order-panel-${escape(order.status)}" aria-live="polite">${orderPanel(order, concert)}</section></div>${order.status === 'paid' ? `<section class="order-tickets" aria-labelledby="tickets-title"><h2 id="tickets-title">Vé của bạn</h2><div id="ticket-results">${ticketCards()}</div></section>` : ''}</div>`;
}

async function loadOrderTickets(version) {
  const view = orderView;
  if (!view) return;
  try {
    const tickets = await api.orderTickets(view.order.id);
    const withQr = await Promise.all(tickets.map(async (ticket) => {
      if (!ticket.qr_code) return { ...ticket, qrUrl: null };
      try {
        const url = URL.createObjectURL(await api.ticketQr(view.order.id, ticket.id));
        view.qrUrls.push(url);
        return { ...ticket, qrUrl: url };
      } catch {
        return { ...ticket, qrUrl: null };
      }
    }));
    if (orderView !== view || version !== pageVersion) {
      view.qrUrls.forEach((url) => URL.revokeObjectURL(url));
      return;
    }
    view.tickets = withQr;
  } catch (error) {
    if (orderView !== view) return;
    view.tickets = error;
  }
  const container = document.querySelector('#ticket-results');
  if (container) container.innerHTML = ticketCards();
}

// Hỏi trạng thái đơn định kỳ đến khi nó rời `held` (cổng thanh toán đã phản hồi, hoặc đơn hết hạn).
function watchOrder(version) {
  const view = orderView;
  if (!view || view.watching) return;
  view.watching = true;
  const tick = async () => {
    if (orderView !== view || version !== pageVersion) return;
    try {
      const fresh = await api.order(view.order.id);
      if (orderView !== view || version !== pageVersion) return;
      if (fresh.status !== view.order.status) {
        view.watching = false;
        view.order = fresh;
        onOrderSettled(view, version);
        return;
      }
    } catch (error) {
      if (error.code !== 'NetworkError') { view.watching = false; return; }
    }
    setTimeout(tick, ORDER_POLL_MS);
  };
  setTimeout(tick, ORDER_POLL_MS);
}

function onOrderSettled(view, version) {
  paintOrderView();
  const outcome = {
    paid: ['Thanh toán thành công! Vé và mã QR của bạn đã được xuất.', 'success'],
    failed: ['Thanh toán không thành công. Vé đã được hoàn về kho.', 'error'],
    expired: ['Hết thời gian giữ chỗ. Vé đã được trả lại kho.', 'info'],
    cancelled: ['Đơn đã được huỷ. Vé đã được trả lại kho.', 'info'],
  }[view.order.status];
  if (outcome) showNotice(...outcome);
  if (view.order.status === 'paid') loadOrderTickets(version);
}

// Đồng hồ giữ chỗ: cập nhật mỗi giây mọi bộ đếm đang hiển thị.
function updateCountdowns() {
  document.querySelectorAll('[data-countdown]').forEach((element) => {
    const left = secondsLeft(element.dataset.countdown);
    element.textContent = clockText(left);
    const box = element.closest('.countdown');
    box?.classList.toggle('is-urgent', left > 0 && left <= 120);
    box?.classList.toggle('is-over', left === 0);
  });
  document.querySelectorAll('[data-action="pay-order"][data-expires]').forEach((button) => {
    if (secondsLeft(button.dataset.expires) === 0) button.disabled = true;
  });
  // Trang chi tiết hết giờ: báo cho người dùng và chờ server chuyển đơn sang `expired`.
  if (orderView?.order.status === 'held' && !holdActive(orderView.order) && !orderView.expiredShown) {
    orderView.expiredShown = true;
    paintOrderView();
    watchOrder(orderView.version);
  }
}

function findOrder(id) {
  if (orderView?.order.id === id) return { order: orderView.order, concert: orderView.ticket?.concert };
  const order = orderData.find((item) => item.id === id);
  return { order, concert: order && ticketLookup.get(order.ticket_type_id)?.concert };
}

// Chuyển sang trang của cổng thanh toán ngay trong tab này (như web đặt vé xem phim).
// Thanh toán xong, cổng đưa khách về lại trang đơn #/orders/{id}.
function goToGateway(payment) {
  window.location.assign(payment.payment_url);
}

// Thanh toán đơn đang giữ chỗ (từ trang đơn hoặc "Vé của tôi"): gọi lại khi đã có giao dịch
// đang chờ thì server trả lại đúng giao dịch cũ, nên bấm nhiều lần cũng không trừ tiền hai lần.
function payOrder(id) {
  const { order } = findOrder(id);
  if (!order || !holdActive(order) || mutationBusy) return;
  mutationBusy = true;
  document.querySelectorAll(`[data-action="pay-order"][data-id="${id}"]`).forEach((button) => {
    button.disabled = true;
    button.textContent = 'Đang chuyển tới cổng thanh toán…';
  });
  api.payOrder(id).then(goToGateway).catch(async (error) => {
    mutationBusy = false;
    showNotice(error.message, 'error');
    await renderRoute();
  });
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

// Đặt vé một bước, không hộp xác nhận: giữ chỗ (12 phút) → tạo giao dịch → chuyển ngay tới cổng
// thanh toán. Thông tin đơn (hạng vé, số lượng, tổng tiền) đã hiện sẵn trong khung chọn vé.
async function bookTicket() {
  if (!detail || mutationBusy || document.querySelector('#book-button').disabled) return;
  if (!getSession()) {
    navigate(`/login?next=${encodeURIComponent(path())}`);
    showNotice('Đăng nhập để tiếp tục giữ chỗ. Lựa chọn của bạn đã được giữ lại.');
    return;
  }
  const { concert } = detail;
  const ticket = detail.tickets.find((item) => item.id === detail.selectedId);
  const quantity = Number(document.querySelector('#quantity').value);
  const user = getSession()?.email;
  if (pendingBooking?.user !== user || pendingBooking.ticketId !== ticket.id || pendingBooking.quantity !== quantity) {
    pendingBooking = { user, ticketId: ticket.id, quantity, key: newIdempotencyKey() };
  }
  const { key } = pendingBooking;
  const button = document.querySelector('#book-button');
  // Lỗi hiện ngay dưới nút (người dùng đang nhìn ở đây), không chỉ ở thông báo đầu trang.
  // Tìm lại phần tử mỗi lần vì khung đặt vé có thể vừa được vẽ lại.
  const showBookError = (message) => {
    const element = document.querySelector('#book-error');
    if (!element) { showNotice(message, 'error'); return; }
    element.textContent = message;
    element.hidden = false;
  };
  document.querySelector('#book-error').hidden = true;
  mutationBusy = true;
  button.disabled = true;
  button.textContent = 'Đang giữ chỗ cho bạn…';

  let order;
  try {
    let orderRequest;
    for (let attempt = 0; ; attempt += 1) {
      try {
        const pass = waitingRoom?.concertId === concert.id && waitingRoom.status === 'admitted' ? waitingRoom.pass_token : undefined;
        orderRequest = await api.createOrder({ ticket_type_id: ticket.id, quantity }, key, pass);
        break;
      } catch (error) {
        // Retry an toàn vì dùng lại cùng khoá: server trả lại đúng yêu cầu đã xếp hàng.
        if (error.code !== 'NetworkError' || attempt >= BOOKING_RETRIES) throw error;
        await new Promise((resolve) => setTimeout(resolve, 500 * 2 ** attempt));
      }
    }
    // Yêu cầu đã vào hàng đợi; chờ worker giữ chỗ xong (hỏi trạng thái định kỳ).
    order = await api.waitForOrder(orderRequest);
    if (pendingBooking?.key === key) pendingBooking = null;
    drafts.delete(concert.id);
  } catch (error) {
    mutationBusy = false;
    // Lỗi mạng hoặc yêu cầu còn đang chờ xử lý: GIỮ pendingBooking để lần bấm lại dùng cùng
    // khoá (server trả lại đúng yêu cầu cũ). Lỗi khác là kết quả chắc chắn nên lần sau dùng khoá mới.
    const unresolved = error.code === 'NetworkError' || error.code === 'OrderPending' || error.status === 503;
    if (!unresolved && pendingBooking?.key === key) pendingBooking = null;
    if (error.code === 'OrderPending') {
      navigate('/my-orders');
      showNotice(error.message, 'info');
    } else if (error.code === 'SoldOut' || error.code === 'SaleNotOpen') {
      await renderRoute(); // tải lại số vé còn
      showBookError(error.message);
    } else if (error.code === 'WaitingRoomRequired') {
      // Thẻ vào cửa thiếu hoặc đã hết hạn: quay lại hàng chờ.
      if (detail) syncBooking();
      showBookError(error.message);
      if (detail?.concert.id === concert.id) syncWaitingRoom(concert.id, pageVersion);
    } else {
      if (detail) syncBooking();
      showBookError(error.code === 'NetworkError'
        ? 'Kết nối đang gián đoạn nên chưa xác nhận được việc đặt vé. Bạn có thể bấm đặt vé lại — hệ thống sẽ không tạo đơn trùng.'
        : error.message);
    }
    return;
  }

  // Đã giữ chỗ: tạo giao dịch và chuyển ngay tới cổng thanh toán.
  button.textContent = 'Đang chuyển tới cổng thanh toán…';
  try {
    goToGateway(await api.payOrder(order.id));
  } catch (error) {
    mutationBusy = false;
    navigate(`/orders/${order.id}`);
    showNotice(`Đã giữ chỗ đơn #${order.id} nhưng chưa mở được cổng thanh toán (${error.message}) Bạn có thể bấm “Thanh toán” trước khi hết ${HOLD_MINUTES} phút giữ chỗ.`, 'error');
  }
}

// Huỷ đơn có hai trường hợp:
//  1. Đang giữ chỗ, chưa trả tiền → huỷ giữ chỗ, vé về kho, không trừ tiền.
//  2. Đã thanh toán → huỷ vé, mã QR bị vô hiệu, hoàn toàn bộ tiền (chỉ trước giờ diễn REFUND_CUTOFF_HOURS).
// Cả hai đều gọi DELETE /orders/{id}; server tự chọn nhánh theo trạng thái đơn.
function cancelOrder(id) {
  const { order, concert } = findOrder(id);
  if (!order) return;
  const paid = order.status === 'paid';
  if (order.status !== 'held' && !(paid && refundOpen(order, concert))) return;
  const content = paid
    ? `<p>Bạn muốn huỷ ${order.quantity} vé của đơn #${id}?</p><ul class="cancel-effects"><li>Mã QR của các vé bị vô hiệu ngay, không dùng để vào cổng được nữa.</li><li>Hoàn toàn bộ <strong>${money(order.total_amount)}</strong> về phương thức thanh toán ban đầu.</li><li>Vé được trả lại kho cho người khác đặt.</li></ul>`
    : `<p>Bạn muốn huỷ giữ chỗ ${order.quantity} vé của đơn #${id}?</p><ul class="cancel-effects"><li>Vé được trả lại kho ngay, bạn không bị trừ tiền.</li><li>Nếu bạn đã trả tiền ở cổng thanh toán, hệ thống sẽ tự động hoàn lại.</li></ul>`;
  openConfirmation({
    title: paid ? `Huỷ vé & hoàn tiền đơn #${id}?` : `Huỷ giữ chỗ đơn #${id}?`,
    label: paid ? 'Huỷ vé & hoàn tiền' : 'Huỷ giữ chỗ',
    danger: true,
    content,
    action: async () => {
      try {
        await api.cancelOrder(id);
        dialog.close();
        await renderRoute();
        showNotice(paid
          ? `Đã huỷ đơn #${id}. ${money(order.total_amount)} sẽ được hoàn về phương thức thanh toán ban đầu.`
          : `Đã huỷ giữ chỗ đơn #${id}. Vé đã được trả lại kho.`, 'success');
      } catch (error) {
        if (['InvalidState', 'RefundWindowClosed', 'NetworkError'].includes(error.code)) {
          dialog.close();
          await renderRoute();
          showNotice(error.code === 'NetworkError' ? 'Kết nối gián đoạn. Kiểm tra trạng thái đơn trước khi huỷ lại.' : error.message, 'error');
        } else throw error;
      }
    },
  });
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
    exploreDiscovery();
  }
  if (action === 'feature-event') {
    featuredIndex = Number(button.dataset.index);
    renderFeatured();
    document.querySelector(`[data-action="feature-event"][data-index="${featuredIndex}"]`)?.focus({ preventScroll: true });
  }
  if (action === 'rail-prev' || action === 'rail-next') {
    const rail = document.querySelector('#featured-rail');
    rail?.scrollBy({ left: (action === 'rail-next' ? 1 : -1) * (rail.clientWidth * .8), behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
  }
  if (action === 'sale-chip') {
    document.querySelector('#sale-filter').value = button.dataset.value;
    renderConcertList();
  }
  if (action === 'all-chip' || action === 'city-chip' || action === 'soon-chip') {
    if (action === 'all-chip') {
      discoveryCity = '';
      discoverySoon = false;
      document.querySelector('#sale-filter').value = 'all';
      document.querySelector('#venue-filter').value = '';
    }
    if (action === 'city-chip') {
      discoveryCity = discoveryCity === button.dataset.city ? '' : button.dataset.city;
      document.querySelector('#venue-filter').value = '';
    }
    if (action === 'soon-chip') discoverySoon = !discoverySoon;
    renderConcertList();
  }
  if (action === 'location-discovery' || action === 'discover-soon') {
    discoveryCity = action === 'location-discovery' ? button.dataset.city : '';
    discoverySoon = action === 'discover-soon';
    discoveryQuery = '';
    discoveryVenue = '';
    discoverySale = 'all';
    if (document.querySelector('#search')) {
      document.querySelector('#search').value = '';
      document.querySelector('#nav-search').value = '';
      document.querySelector('#sale-filter').value = 'all';
      document.querySelector('#venue-filter').value = '';
      renderConcertList();
      exploreDiscovery();
    } else {
      pendingDiscovery = true;
      navigate('/');
    }
  }
  if (action === 'reset-discovery') {
    discoveryCity = '';
    discoverySoon = false;
    document.querySelector('#search').value = '';
    document.querySelector('#nav-search').value = '';
    document.querySelector('#sale-filter').value = 'all';
    document.querySelector('#venue-filter').value = '';
    renderConcertList();
  }
  if (action === 'discover-open' || action === 'discover-upcoming') {
    discoveryCity = '';
    discoverySoon = false;
    discoverySale = action === 'discover-open' ? 'open' : 'upcoming';
    if (document.querySelector('#sale-filter')) {
      document.querySelector('#sale-filter').value = discoverySale;
      renderConcertList();
      exploreDiscovery();
    } else {
      pendingDiscovery = true;
      navigate('/');
    }
  }
  if (action === 'logout') {
    setSession(null);
    orderData = [];
    ticketLookup.clear();
    drafts.clear();
    closeOrderView();
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
  if ((action === 'ticket-increase' || action === 'ticket-decrease') && detail) {
    const ticket = detail.tickets.find((item) => item.id === Number(button.dataset.id));
    if (ticket?.remaining > 0) {
      const input = document.querySelector('#quantity');
      const current = detail.selectedId === ticket.id ? Number(input.value) || 1 : 0;
      detail.selectedId = ticket.id;
      input.value = String(Math.max(1, Math.min(Math.min(10, ticket.remaining), current + (action === 'ticket-increase' ? 1 : -1))));
      syncBooking();
    }
  }
  if (action === 'book') bookTicket();
  if (action === 'rejoin-waiting-room' && detail) syncWaitingRoom(detail.concert.id, pageVersion);
  if (action === 'cancel-order') cancelOrder(Number(button.dataset.id));
  if (action === 'pay-order') payOrder(Number(button.dataset.id));
  if (action === 'reload-tickets' && orderView) {
    orderView.tickets = null;
    document.querySelector('#ticket-results').innerHTML = ticketCards();
    loadOrderTickets(orderView.version);
  }
});

document.addEventListener('input', (event) => {
  if (event.target.id === 'search') {
    document.querySelector('#nav-search').value = event.target.value;
    renderConcertList();
  }
  if (event.target.id === 'quantity') syncBooking();
  if (event.target.dataset.ticketQuantity && detail) {
    const ticket = detail.tickets.find((item) => item.id === Number(event.target.dataset.ticketQuantity));
    if (ticket?.remaining > 0 && (Number(event.target.value) > 0 || detail.selectedId === ticket.id)) {
      detail.selectedId = ticket.id;
      document.querySelector('#quantity').value = event.target.value;
      syncBooking();
    }
  }
});
document.addEventListener('change', (event) => {
  if (event.target.dataset.ticketQuantity) syncBooking();
  if (event.target.id === 'sale-filter' || event.target.id === 'venue-filter') renderConcertList();
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
  if (event.target.id === 'nav-search-form') {
    event.preventDefault();
    discoveryQuery = document.querySelector('#nav-search').value;
    if (document.querySelector('#search')) {
      document.querySelector('#search').value = discoveryQuery;
      renderConcertList();
      exploreDiscovery();
    } else {
      pendingDiscovery = true;
      navigate('/');
    }
  }
  if (event.target.id === 'discovery-form') {
    event.preventDefault();
    renderConcertList();
    exploreDiscovery();
  }
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
// Bấm "Quay lại" từ cổng thanh toán: trình duyệt có thể khôi phục trang từ bộ nhớ đệm (bfcache)
// với các nút còn đang khoá. Mở khoá và tải lại trạng thái đơn.
window.addEventListener('pageshow', (event) => {
  if (!event.persisted) return;
  mutationBusy = false;
  renderRoute();
});
setInterval(updateCountdowns, 1000);
setInterval(() => {
  if (detail) syncBooking();
  else if ((path() === '/' || path() === '/concerts') && document.querySelector('#concert-results')) renderConcertList();
}, 30000);
renderRoute();
refreshProfile();

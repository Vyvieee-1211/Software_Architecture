import { api, getSession, setSession, getSavedName, saveName } from './api.js';
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

const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const money = (value) => new Intl.NumberFormat('vi-VN', { style: 'currency', currency: 'VND', maximumFractionDigits: 0 }).format(Number(value));
const date = (value) => new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`);
const dateText = (value, options = {}) => !value || Number.isNaN(date(value).getTime()) ? 'Thời gian đang cập nhật' : new Intl.DateTimeFormat('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', ...options }).format(date(value));
const isOpen = (concert) => date(concert.sale_open_time).getTime() <= Date.now();
const saleBadge = (concert) => `<span class="badge ${isOpen(concert) ? 'badge-success' : 'badge-muted'}">${isOpen(concert) ? 'Đang mở bán' : 'Sắp mở bán'}</span>`;
const path = () => (location.hash.slice(1) || '/').split('?')[0];
const safeNext = (value) => /^\/(?:concerts\/\d+|my-orders|manage(?:\/\d+)?)?$/.test(value || '') ? value : '/';
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
    const active = link.dataset.nav === 'manage' ? path().startsWith('/manage') : link.dataset.nav === 'orders' ? path() === '/my-orders' : link.dataset.nav === 'concerts' ? path() === '/concerts' || path().startsWith('/concerts/') : path() === '/';
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
  if (!mutationBusy) dialog.close();
  const route = path();
  if ((route === '/my-orders' || /^\/manage(?:\/\d+)?$/.test(route)) && !getSession()) {
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
      <aside class="booking-panel" aria-labelledby="booking-title"><div class="booking-panel-header"><span class="eyebrow">YOUR TICKET TO THE MOMENT</span><h2 id="booking-title">Chọn vé của bạn</h2><p>Mỗi đơn tối đa 10 vé · Một hạng vé mỗi đơn</p></div><div id="sale-notice">${!isOpen(concert) ? `<div class="notice notice-info">Vé mở bán lúc ${escape(dateText(concert.sale_open_time))} (giờ Việt Nam).</div>` : ''}</div><div class="ticket-list" role="radiogroup" aria-label="Chọn hạng vé">${tickets.length ? tickets.map((ticket) => `<div class="ticket-option ${selected?.id === ticket.id ? 'is-selected' : ''}"><label class="ticket-option-label"><input type="radio" name="ticket" value="${ticket.id}" ${selected?.id === ticket.id ? 'checked' : ''} ${ticket.remaining <= 0 ? 'disabled' : ''}><span class="ticket-copy"><strong>${escape(ticket.name)}</strong><span class="ticket-stock">${ticket.remaining > 0 ? `Còn ${ticket.remaining.toLocaleString('vi-VN')} vé` : 'Đã hết vé'}</span></span></label><div class="ticket-option-bottom"><span class="ticket-price">${money(ticket.price)}</span>${rowQuantity(ticket, selected?.id, quantity)}</div></div>`).join('') : empty('Chưa có hạng vé', 'Thông tin vé sẽ được cập nhật sau.')}</div><div class="booking-summary"><div class="summary-row"><span>Hạng vé</span><strong id="summary-ticket"></strong></div><div class="summary-row"><span>Đơn giá</span><strong id="summary-price"></strong></div><div class="selected-quantity"><label for="quantity">Số lượng đã chọn</label><div class="quantity-control"><button type="button" data-action="decrease" aria-label="Giảm số lượng">−</button><input id="quantity" type="number" min="1" max="10" step="1" value="${quantity}" inputmode="numeric" aria-describedby="quantity-error"><button type="button" data-action="increase" aria-label="Tăng số lượng">+</button></div></div><p class="form-error" id="quantity-error" hidden></p><div class="summary-total"><span>Tổng tiền</span><strong id="summary-total"></strong></div><button id="book-button" class="button button-primary button-full" data-action="book" type="button"></button><p class="booking-footnote">Đơn vé được xác nhận ngay khi đặt thành công.</p></div></aside>
    </div></div>`;
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
  button.textContent = !open ? 'Chưa đến thời gian mở bán' : !ticket ? 'Chưa có vé khả dụng' : getSession() ? 'Đặt vé ↗' : 'Đăng nhập để đặt vé ↗';
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
  document.title = 'Đơn vé của tôi — HIT THE VIBE';
  main.innerHTML = `<div class="container orders-page"><div class="page-heading"><p class="eyebrow">NHỮNG CUỘC HẸN SẮP TỚI</p><h1>Đơn vé của tôi</h1><p class="lead">Xem lại và quản lý vé concert của bạn tại đây.</p></div>${incomplete ? '<div class="notice notice-info">Chưa tải được một số tên concert hoặc hạng vé. <button class="button button-link button-small" data-action="retry">Thử lại</button></div>' : ''}<div class="toolbar"><label for="order-filter">Trạng thái đơn</label><select id="order-filter"><option value="all">Tất cả đơn vé</option><option value="confirmed">Đã xác nhận</option><option value="cancelled">Đã huỷ</option></select><button class="button button-secondary button-small" data-action="retry">Làm mới</button></div><div id="order-results"></div></div>`;
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
  if (action === 'cancel-order') cancelOrder(Number(button.dataset.id));
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
setInterval(() => {
  if (detail) syncBooking();
  else if ((path() === '/' || path() === '/concerts') && document.querySelector('#concert-results')) renderConcertList();
}, 30000);
renderRoute();
refreshProfile();

import { api } from './api.js';

const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const currency = (value) => new Intl.NumberFormat('vi-VN', { style: 'currency', currency: 'VND' }).format(Number(value));
const localTime = (value) => {
  if (!value) return '';
  const time = new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`);
  if (Number.isNaN(time.getTime())) return '';
  return new Date(time.getTime() + 7 * 3600000).toISOString().slice(0, 16);
};
const fields = (ticket = {}) => `<div class="manage-ticket-fields"><label class="field">Tên hạng vé<input name="name" value="${escape(ticket.name)}" placeholder="VIP / Standard / GA" maxlength="50" required></label><label class="field">Giá vé (VND)<input name="price" type="number" value="${escape(ticket.price ?? '')}" min="0" max="9999999999.99" step="0.01" required></label><label class="field">Tổng số vé<input name="total_quantity" type="number" value="${escape(ticket.total_quantity ?? '')}" min="1" max="10000000" step="1" required></label></div>`;
const ticketBody = (form) => {
  const values = new FormData(form);
  return { name: values.get('name').trim(), price: values.get('price'), total_quantity: Number(values.get('total_quantity')) };
};

export async function renderManagement(main, id, current, notify) {
  const user = await api.me();
  if (!current()) return;
  if (!user.is_admin) {
    main.innerHTML = '<div class="container state-page"><h1>Quyền quản trị cần thiết</h1><p>Tài khoản này có thể mua vé nhưng không có quyền quản lý concert.</p><a class="button button-primary" href="#/concerts">Khám phá concert</a></div>';
    return;
  }
  const [concerts, tickets] = await Promise.all([api.concerts(), id ? api.ticketTypes(id) : Promise.resolve([])]);
  if (!current()) return;
  const concert = concerts.find((item) => item.id === id);
  if (id && !concert) throw new Error('Không tìm thấy concert cần quản lý.');
  document.title = 'Quản lý concert — HIT THE VIBE';
  main.innerHTML = `<section class="container manage-page"><div class="manage-heading"><div><p class="eyebrow">HIT THE VIBE / QUẢN TRỊ</p><h1>Đưa đêm nhạc<br>lên sân khấu.</h1><p>Tạo sự kiện, thiết lập hạng vé và quản lý thông tin concert.</p></div><a class="button button-primary" href="#/manage">＋ Tạo concert mới</a></div><div class="manage-layout"><aside class="manage-sidebar"><div class="manage-list-title"><h2>Concert của bạn</h2><span>${concerts.length} sự kiện</span></div>${concerts.length ? concerts.map((item) => `<a class="manage-event ${item.id === id ? 'selected' : ''}" href="#/manage/${item.id}"><span class="manage-event-mark">LIVE<br>#${item.id}</span><span><strong>${escape(item.name)}</strong><small>${escape(item.artist || 'Chưa có nghệ sĩ')}</small><small>${escape(item.venue || 'Chưa có địa điểm')}</small></span></a>`).join('') : '<p>Chưa có concert. Hãy tạo sự kiện đầu tiên.</p>'}</aside><div class="manage-editor"><div class="manage-editor-heading"><div><p class="eyebrow">${id ? `SỰ KIỆN #${id}` : 'SỰ KIỆN MỚI'}</p><h2>${id ? 'Chỉnh sửa concert' : 'Tạo concert'}</h2></div>${id ? `<a class="button button-secondary button-small" href="#/concerts/${id}">Xem trang concert ↗</a>` : ''}</div><form id="concert-editor"><div class="manage-form-grid"><label class="field manage-wide">Tên concert<input name="name" value="${escape(concert?.name)}" maxlength="255" required placeholder="Tên đêm nhạc của bạn"></label><label class="field">Nghệ sĩ<input name="artist" value="${escape(concert?.artist)}" maxlength="255" placeholder="Tên nghệ sĩ"></label><label class="field">Địa điểm<input name="venue" value="${escape(concert?.venue)}" maxlength="255" placeholder="Địa điểm, thành phố"></label><label class="field">Thời gian diễn ra<input name="start_time" type="datetime-local" value="${localTime(concert?.start_time)}" required></label><label class="field">Thời gian mở bán<input name="sale_open_time" type="datetime-local" value="${localTime(concert?.sale_open_time)}" required></label></div><p class="manage-hint">Thời gian theo múi giờ Việt Nam (UTC+7). Thời điểm mở bán không được sau thời điểm diễn ra.</p>${!id ? `<div class="manage-section-title"><h3>Hạng vé mở bán</h3><button type="button" class="button button-secondary button-small" data-manage="add-row">＋ Thêm hạng vé</button></div><div id="new-ticket-rows"><div class="manage-new-ticket">${fields()}<button type="button" class="button button-link button-small" data-manage="remove-row">Bỏ hạng vé</button></div></div>` : ''}<p class="form-error" role="alert" hidden></p><button class="button button-primary" type="submit">${id ? 'Lưu thông tin concert' : 'Tạo concert & mở bán vé'} ↗</button></form>${id ? `<div class="manage-section-title"><h3>Quản lý hạng vé</h3><span>${tickets.length} hạng vé</span></div><p class="manage-hint">Thay đổi tổng số vé sẽ điều chỉnh số vé còn lại và giữ nguyên số vé đã đặt. Hạng vé có lịch sử đặt không thể đổi giá hoặc xóa.</p><div class="manage-ticket-list">${tickets.map((ticket) => `<form class="manage-ticket-form" data-ticket-id="${ticket.id}"><div class="manage-ticket-meta"><strong>${escape(ticket.name)}</strong><span>Còn ${ticket.remaining.toLocaleString('vi-VN')} / ${ticket.total_quantity.toLocaleString('vi-VN')} vé · ${currency(ticket.price)}</span></div>${fields(ticket)}<p class="form-error" role="alert" hidden></p><div class="manage-actions"><button class="button button-secondary button-small" type="submit">Lưu hạng vé</button><button class="button button-link button-small" type="button" data-manage="delete-ticket" data-ticket-id="${ticket.id}">Xóa hạng vé</button></div></form>`).join('')}</div><form id="add-ticket-editor" class="manage-ticket-form"><h3>Thêm hạng vé mới</h3>${fields()}<p class="form-error" role="alert" hidden></p><button type="submit" class="button button-primary button-small">＋ Thêm hạng vé</button></form><div class="manage-danger"><div><h3>Xóa concert</h3><p>Chỉ có thể xóa concert chưa có lịch sử đặt vé.</p></div><button class="button button-secondary button-small" type="button" data-manage="delete-concert">Xóa concert</button><p class="form-error" role="alert" hidden></p></div>` : ''}</div></div></section>`;
  const section = main.querySelector('.manage-page');
  let busy = false;
  async function mutate(container, work, destination) {
    if (busy) return;
    busy = true;
    const buttons = [...section.querySelectorAll('button')];
    buttons.forEach((button) => { button.disabled = true; });
    const error = container.querySelector('.form-error');
    if (error) error.hidden = true;
    try {
      const result = await work();
      if (!current()) return;
      notify('Đã lưu thay đổi. Danh sách concert và hạng vé đã được cập nhật.', 'success');
      const target = destination ? destination(result) : `#/manage/${id}`;
      if (location.hash === target) await renderManagement(main, id, current, notify);
      else location.hash = target;
    } catch (failure) {
      if (current() && error) { error.textContent = failure.message; error.hidden = false; }
    } finally {
      busy = false;
      buttons.forEach((button) => { button.disabled = false; });
    }
  }
  section.addEventListener('submit', (event) => {
    event.preventDefault();
    const form = event.target;
    if (form.id === 'concert-editor') {
      const values = new FormData(form);
      const body = {
        name: values.get('name').trim(), artist: values.get('artist').trim() || null, venue: values.get('venue').trim() || null,
        start_time: new Date(`${values.get('start_time')}+07:00`).toISOString(),
        sale_open_time: new Date(`${values.get('sale_open_time')}+07:00`).toISOString(),
      };
      if (body.sale_open_time > body.start_time) {
        const error = form.querySelector('.form-error');
        error.textContent = 'Thời điểm mở bán không được sau thời điểm diễn ra.';
        error.hidden = false;
        return;
      }
      if (!id) body.ticket_types = [...form.querySelectorAll('.manage-new-ticket')].map((row) => ({
        name: row.querySelector('[name="name"]').value.trim(), price: row.querySelector('[name="price"]').value,
        total_quantity: Number(row.querySelector('[name="total_quantity"]').value),
      }));
      mutate(form, () => id ? api.updateConcert(id, body) : api.createConcert(body), (result) => `#/manage/${result.id}`);
    } else if (form.id === 'add-ticket-editor') mutate(form, () => api.createTicket(id, ticketBody(form)));
    else if (form.dataset.ticketId) mutate(form, () => api.updateTicket(id, Number(form.dataset.ticketId), ticketBody(form)));
  });
  section.addEventListener('click', (event) => {
    const button = event.target.closest('[data-manage]');
    if (!button || busy) return;
    if (button.dataset.manage === 'add-row') {
      const rows = section.querySelector('#new-ticket-rows');
      if (rows.children.length >= 30) return;
      rows.insertAdjacentHTML('beforeend', `<div class="manage-new-ticket">${fields()}<button type="button" class="button button-link button-small" data-manage="remove-row">Bỏ hạng vé</button></div>`);
    } else if (button.dataset.manage === 'remove-row') {
      if (section.querySelector('#new-ticket-rows').children.length > 1) button.closest('.manage-new-ticket').remove();
    } else if (button.dataset.manage === 'delete-ticket') {
      if (window.confirm('Xóa hạng vé này? Thao tác chỉ thực hiện được nếu chưa có lịch sử đặt vé.')) mutate(button.closest('form'), () => api.deleteTicket(id, Number(button.dataset.ticketId)));
    } else if (button.dataset.manage === 'delete-concert') {
      if (window.confirm(`Xóa concert “${concert.name}” và toàn bộ hạng vé chưa được đặt?`)) mutate(button.closest('.manage-danger'), () => api.deleteConcert(id), () => '#/manage');
    }
  });
}

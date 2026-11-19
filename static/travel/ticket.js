import {$, openDialog, show} from './ui.js';

export function setupTicket(request, changed) {
  const canvas = $('ticket-canvas');
  const ctx = canvas.getContext('2d');
  const fields = $('ticket-fields');
  let image, crop, masks = [], drag, confirmed, attachment;
  let mode = 'crop';
  let loading = 0;
  let uploading = false;

  function render(outline) {
    if (!image) return;
    if (canvas.width !== crop.w || canvas.height !== crop.h) {
      canvas.width = crop.w;
      canvas.height = crop.h;
    }
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(image, crop.x, crop.y, crop.w, crop.h, 0, 0, crop.w, crop.h);
    ctx.fillStyle = '#151b17';
    for (const rect of masks) ctx.fillRect(rect.x - crop.x, rect.y - crop.y, rect.w, rect.h);
    if (outline) {
      ctx.strokeStyle = '#dc691a';
      ctx.lineWidth = Math.max(2, crop.w / 225);
      ctx.strokeRect(outline.x - crop.x, outline.y - crop.y, outline.w, outline.h);
    }
  }
  function updateUpload() {
    $('upload').disabled = uploading || !image || !$('confirm-mask').checked;
    $('file').disabled = uploading;
  }
  function dirty() {
    $('confirm-mask').checked = false;
    updateUpload();
  }
  function reset() {
    if (!image) return;
    crop = {x: 0, y: 0, w: image.width, h: image.height};
    masks = [];
    dirty();
    render();
  }
  function pickMode(value) {
    mode = value;
    for (const id of ['crop', 'mask']) $(id).setAttribute('aria-pressed', String(id === mode));
  }
  function apply(rect) {
    if (uploading || rect.w < 2 || rect.h < 2) return render();
    if (mode === 'crop') crop = rect;
    else masks.push(rect);
    dirty();
    render();
  }
  function point(event) {
    const bounds = canvas.getBoundingClientRect();
    const unit = (n, max) => Math.round(Math.max(0, Math.min(1, n)) * max);
    return {x: crop.x + unit((event.clientX - bounds.left) / bounds.width, crop.w),
      y: crop.y + unit((event.clientY - bounds.top) / bounds.height, crop.h)};
  }
  function rectFrom(a, b) {
    return {x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(a.x - b.x), h: Math.abs(a.y - b.y)};
  }
  function displayTicket() {
    const text = confirmed && Object.values(confirmed).filter(Boolean).join(' · ');
    $('ticket-summary').textContent = text || (attachment ? '待确认票面字段' : '');
    changed();
  }
  function remove() {
    if (uploading) return;
    loading++;
    image?.close();
    image = attachment = confirmed = null;
    fields.reset();
    $('file').value = '';
    $('ticket-tools').hidden = true;
    show('ticket-feedback', '');
    displayTicket();
  }
  $('open-ticket').addEventListener('click', () => openDialog('ticket-dialog'));
  $('file').addEventListener('change', async () => {
    const file = $('file').files[0];
    if (!file) return;
    const token = ++loading;
    image?.close();
    image = null;
    attachment = confirmed = null;
    fields.reset();
    displayTicket();
    $('ticket-tools').hidden = true;
    show('ticket-feedback', '');
    try {
      if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 12 * 1024 * 1024) {
        throw new Error('请选择不超过 12 MB 的 PNG、JPEG 或 WebP 截图。');
      }
      const bitmap = await createImageBitmap(file);
      if (token !== loading) return bitmap.close();
      if (bitmap.width * bitmap.height > 40000000 || bitmap.width < 2 || bitmap.height < 2) {
        bitmap.close();
        throw new Error('截图尺寸无法处理，请先缩小图片。');
      }
      image = bitmap;
      $('ticket-tools').hidden = false;
      pickMode('crop');
      reset();
    } catch (error) {
      if (token === loading) show('ticket-feedback', error.message || '无法读取截图，可直接填写票面字段。');
    }
  });
  for (const id of ['crop', 'mask']) $(id).addEventListener('click', () => pickMode(id));
  $('reset-image').addEventListener('click', () => { if (!uploading) reset(); });
  $('confirm-mask').addEventListener('change', updateUpload);
  $('apply-rect').addEventListener('click', () => {
    if (!image) return;
    const value = id => Math.min(100, Math.max(0, Number($(id).value))) / 100;
    const x = Math.round(crop.w * value('rect-x'));
    const y = Math.round(crop.h * value('rect-y'));
    apply({x: crop.x + x, y: crop.y + y,
      w: Math.min(crop.w - x, Math.round(crop.w * value('rect-w'))),
      h: Math.min(crop.h - y, Math.round(crop.h * value('rect-h')))});
  });
  canvas.addEventListener('pointerdown', event => {
    if (uploading || drag || event.button !== 0 || !image) return;
    drag = {id: event.pointerId, point: point(event)};
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove', event => {
    if (drag?.id === event.pointerId) render(rectFrom(drag.point, point(event)));
  });
  canvas.addEventListener('pointerup', event => {
    if (drag?.id !== event.pointerId) return;
    const rect = rectFrom(drag.point, point(event));
    drag = null;
    apply(rect);
  });
  canvas.addEventListener('pointercancel', () => { drag = null; render(); });
  $('upload').addEventListener('click', async () => {
    if ($('upload').disabled) return;
    uploading = true;
    updateUpload();
    try {
      render();
      const exportCanvas = document.createElement('canvas');
      const scale = Math.min(1, 1800 / Math.max(canvas.width, canvas.height));
      exportCanvas.width = Math.max(1, Math.round(canvas.width * scale));
      exportCanvas.height = Math.max(1, Math.round(canvas.height * scale));
      exportCanvas.getContext('2d').drawImage(canvas, 0, 0, exportCanvas.width, exportCanvas.height);
      const blob = await new Promise(resolve => exportCanvas.toBlob(resolve, 'image/jpeg', 0.92));
      if (!blob) throw new Error('截图导出失败，请重试。');
      const form = new FormData();
      form.append('file', blob, 'redacted-ticket.jpg');
      const data = await request('/api/travel/attachments', {method: 'POST', body: form});
      attachment = data.attachment_id;
      confirmed = null;
      for (const input of fields.querySelectorAll('input[name]')) {
        input.value = data.suggested_fields?.[input.name] ?? '';
      }
      displayTicket();
      show('ticket-feedback', '请核对识别内容。可修改或手动补充，再确认票面信息。');
      fields.querySelector('input').focus();
    } catch (error) {
      show('ticket-feedback', `${error.message} 可以手动填写票面信息。`);
    } finally {
      uploading = false;
      dirty();
    }
  });
  fields.addEventListener('submit', event => {
    event.preventDefault();
    if (uploading) return;
    confirmed = Object.fromEntries([...new FormData(fields)].filter(([, value]) => value.trim()));
    displayTicket();
    $('ticket-dialog').close();
  });
  $('remove-ticket').addEventListener('click', remove);
  return () => ({attachmentIds: confirmed && attachment ? [attachment] : [], ticketFields: confirmed, pending: uploading || (!!attachment && !confirmed)});
}

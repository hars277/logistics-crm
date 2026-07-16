(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const form = $('peshgiForm');
  if (!form) return;
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';

  const F = {
    tcsPhotos: $('tcsPhotos'), tcsNo: $('tcsNo'), vehicleNo: $('vehicleNo'),
    driverName: $('driverName'), driverMobile: $('driverMobile'), fromPlace: $('fromPlace'),
    toPlace: $('toPlace'), entryDate: $('entryDate'), labour: $('labour'), fuelAmount: $('fuelAmount'),
    roti: $('roti'), babu: $('babu'), hold: $('hold'), totalPeshgi: $('totalPeshgi'),
    paymentReceiving: $('paymentReceiving'), qrPhoto: $('qrPhoto'),
  };
  const preview = $('messagePreview');
  const liveTotal = $('liveTotal');
  const fuelLabel = $('fuelAmountLabel');

  const clean = (v) => String(v || '').trim();
  const num = (v) => Number(v || 0) || 0;
  const money = (v) => '₹' + Math.round(num(v)).toLocaleString('en-IN');
  const mb = (b) => (b / 1024 / 1024).toFixed(1) + ' MB';

  function toast(msg, type = 'success') {
    let stack = document.querySelector('.toast-stack');
    if (!stack) { stack = document.createElement('div'); stack.className = 'toast-stack'; document.body.appendChild(stack); }
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = msg;
    stack.appendChild(el);
    setTimeout(() => el.remove(), 4200);
  }

  const fuelType = () => (document.querySelector('input[name="fuel_type"]:checked') || {}).value || 'CNG';
  const isDiesel = () => fuelType().toLowerCase() === 'diesel';

  function formatDate(v) {
    if (!v) return '';
    const p = String(v).split('-');
    return p.length === 3 ? `${p[2]}-${p[1]}-${p[0]}` : v;
  }
  const currentTime = () => new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });

  function calculateTotal() {
    const fuelCost = isDiesel() ? 0 : num(F.fuelAmount.value);
    const total = num(F.labour.value) + fuelCost + num(F.roti.value) + num(F.babu.value) + num(F.hold.value);
    F.totalPeshgi.value = String(Math.round(total));
    liveTotal.textContent = money(total);
    return total;
  }

  function getData() {
    const total = calculateTotal();
    return {
      dateTime: [formatDate(F.entryDate.value), currentTime()].filter(Boolean).join(' | '),
      tcsNo: clean(F.tcsNo.value), vehicleNo: clean(F.vehicleNo.value).toUpperCase(),
      driverName: clean(F.driverName.value), driverMobile: clean(F.driverMobile.value),
      fromPlace: clean(F.fromPlace.value), toPlace: clean(F.toPlace.value),
      fuelType: fuelType(), labour: num(F.labour.value), fuelAmount: num(F.fuelAmount.value),
      roti: num(F.roti.value), babu: num(F.babu.value), hold: num(F.hold.value), totalPeshgi: total,
      paymentReceiving: clean(F.paymentReceiving.value),
      tcsFileCount: (F.tcsPhotos.files || []).length, qrFileCount: (F.qrPhoto.files || []).length,
      tpNo: form.dataset.tpNo || '',
    };
  }

  function buildMessage(d) {
    const lines = ['🚚 *TCI Peshgi Expense*'];
    const add = (label, value) => {
      const t = clean(value);
      if (!t || t === '0' || t === '₹0') return;
      lines.push(`*${label}:* ${t}`);
    };
    if (d.tpNo) add('TP No', d.tpNo);
    add('Date & Time', d.dateTime);
    add('TCS/THC/LR No.', d.tcsNo);
    add('Vehicle No.', d.vehicleNo);
    add('Driver Name', d.driverName);
    add('Driver Mob. No.', d.driverMobile);
    add('From', d.fromPlace);
    add('To', d.toPlace);
    add('Fuel Type', d.fuelType);

    const exp = [];
    const addExp = (l, v) => { if (v) exp.push(`*${l}:* ${v}`); };
    if (d.labour > 0) addExp('Labour', money(d.labour));
    if (d.fuelAmount > 0) addExp(d.fuelType === 'Diesel' ? 'Diesel Ltr' : 'CNG Price', d.fuelType === 'Diesel' ? `${d.fuelAmount} Ltr` : money(d.fuelAmount));
    if (d.roti > 0) addExp('Roti', money(d.roti));
    if (d.babu > 0) addExp('Babu', money(d.babu));
    if (d.hold > 0) addExp('Hold', money(d.hold));
    if (d.totalPeshgi > 0) addExp('Total Peshgi', money(d.totalPeshgi));
    if (exp.length) lines.push('', '*Expense Details*', ...exp);

    add('Payment Receiving / UPI', d.paymentReceiving);

    const att = [];
    if (d.tcsFileCount > 0) att.push(`*TCS/THC/LR Photos:* ${d.tcsFileCount} attached`);
    if (d.qrFileCount > 0) att.push('*QR Image:* attached');
    if (att.length) lines.push('', '*Attachments*', ...att);
    return lines.join('\n');
  }

  function updatePreview() {
    if (isDiesel()) { fuelLabel.innerHTML = 'Diesel Ltr <em class="req">*</em>'; F.fuelAmount.placeholder = '0 Ltr'; }
    else { fuelLabel.innerHTML = 'CNG Price <em class="req">*</em>'; F.fuelAmount.placeholder = '0'; }
    preview.textContent = buildMessage(getData());
  }

  function renderFileList(input, targetId) {
    const box = $(targetId);
    box.innerHTML = '';
    Array.from(input.files || []).forEach((file) => {
      const row = document.createElement('div');
      row.className = 'file-item';
      row.innerHTML = `<span>${file.name}</span><em>${mb(file.size)}</em>`;
      box.appendChild(row);
    });
    updatePreview();
  }

  function getFiles() {
    const tcs = Array.from(F.tcsPhotos.files || []);
    const qr = (F.qrPhoto.files && F.qrPhoto.files[0]) ? [F.qrPhoto.files[0]] : [];
    return [...tcs, ...qr];
  }

  function validateForm() {
    if (!form.checkValidity()) { form.reportValidity(); return false; }
    if (!/^\d{10}$/.test(clean(F.driverMobile.value))) { F.driverMobile.focus(); toast('Driver mobile 10 digit ka hona chahiye', 'error'); return false; }
    return true;
  }

  async function saveToDb() {
    const payload = {
      tcs_no: clean(F.tcsNo.value), vehicle_no: clean(F.vehicleNo.value).toUpperCase(),
      driver_name: clean(F.driverName.value), driver_mobile: clean(F.driverMobile.value),
      from_place: clean(F.fromPlace.value), to_place: clean(F.toPlace.value),
      entry_date: clean(F.entryDate.value), fuel_type: fuelType(),
      labour: clean(F.labour.value), fuel_amount: clean(F.fuelAmount.value),
      roti: clean(F.roti.value), babu: clean(F.babu.value), hold: clean(F.hold.value),
      total_peshgi: clean(F.totalPeshgi.value), payment_receiving: clean(F.paymentReceiving.value),
    };
    const res = await fetch('/api/peshgi/save', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || data.ok === false) { const e = new Error(data.message || 'Save failed'); e.data = data; throw e; }
    return data;
  }

  async function shareOnWhatsApp(message) {
    const files = getFiles().filter((f) => f.size <= 100 * 1024 * 1024);
    try {
      if (navigator.share) {
        if (files.length && navigator.canShare && navigator.canShare({ files })) {
          await navigator.share({ title: 'TCI Peshgi Expense', text: message, files });
          return;
        }
        await navigator.share({ title: 'TCI Peshgi Expense', text: message });
        return;
      }
      window.open('https://wa.me/?text=' + encodeURIComponent(message), '_blank', 'noopener,noreferrer');
    } catch (err) {
      if (err && err.name === 'AbortError') return;
      window.open('https://wa.me/?text=' + encodeURIComponent(message), '_blank', 'noopener,noreferrer');
    }
  }

  let submitting = false;
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (submitting) return;
    if (!validateForm()) return;
    submitting = true;
    const btn = $('peshgiSend');
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = 'Saving…';
    try {
      const saved = await saveToDb();
      form.dataset.tpNo = saved.tp_no || '';
      toast(`Saved ✓ Voucher banaya — TP: ${saved.tp_no}`, 'success');
      const message = buildMessage(getData());
      await shareOnWhatsApp(message);
    } catch (err) {
      const d = err.data || {};
      toast(err.message || 'Save nahi hua', d.code === 'division_mismatch' ? 'warn' : 'error');
    } finally {
      btn.disabled = false; btn.textContent = old; submitting = false;
    }
  });

  $('peshgiCopy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(buildMessage(getData())); toast('Message copied'); }
    catch (_) { toast('Copy not supported', 'error'); }
  });

  $('peshgiClear').addEventListener('click', () => {
    form.reset();
    form.dataset.tpNo = '';
    $('tcsPreview').innerHTML = '';
    $('qrPreview').innerHTML = '';
    F.entryDate.value = new Date().toISOString().slice(0, 10);
    updatePreview();
  });

  // Digits-only, max 10 for mobile.
  F.driverMobile.addEventListener('input', () => { F.driverMobile.value = F.driverMobile.value.replace(/\D/g, '').slice(0, 10); });

  // Auto-fill driver/fuel from the vehicle master when a known vehicle is entered.
  F.vehicleNo.addEventListener('blur', async () => {
    const v = F.vehicleNo.value.replace(/[^a-zA-Z0-9]/g, '').toUpperCase();
    F.vehicleNo.value = v;
    if (v.length < 4) return;
    try {
      const res = await fetch(`/api/vehicle/${encodeURIComponent(v)}`, { credentials: 'same-origin' });
      const data = await res.json();
      if (!data.found) return;
      const a = data.autofill || {};
      if (!F.driverName.value && a.driver_name) F.driverName.value = a.driver_name;
      if (!F.driverMobile.value && a.driver_mobile) F.driverMobile.value = a.driver_mobile;
      if (a.fuel_type) {
        const r = document.querySelector(`input[name="fuel_type"][value="${a.fuel_type === 'DIESEL' ? 'Diesel' : (a.fuel_type === 'CNG' ? 'CNG' : '')}"]`);
        if (r) r.checked = true;
      }
      updatePreview();
    } catch (_) {}
  });

  form.addEventListener('input', updatePreview);
  form.addEventListener('change', (e) => {
    if (e.target === F.tcsPhotos) renderFileList(F.tcsPhotos, 'tcsPreview');
    if (e.target === F.qrPhoto) renderFileList(F.qrPhoto, 'qrPreview');
    updatePreview();
  });

  updatePreview();
})();

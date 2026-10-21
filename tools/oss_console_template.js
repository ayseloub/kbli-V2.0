// === Ambil URAIAN KBLI 2020 dari OSS (v2) ===
// WAJIB dijalankan di Console pada tab oss.go.id (mis. https://oss.go.id/id/kbli).
// TEST = true  -> coba 3 kode dulu buat ngecek.  TEST = false -> semua kode.
(async () => { // v3
  const TEST = false;
  if (!location.hostname.endsWith('oss.go.id')) {
    console.error('⛔ Jalankan di tab oss.go.id (sekarang: ' + location.href + ')'); return;
  }
  const ALL = __CODES__;
  const CODES = TEST ? ALL.slice(0, 3) : ALL;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;
  const getText = async url => { const r = await fetch(url, { credentials: 'include' }); if (!r.ok) throw new Error(url + ' HTTP ' + r.status); return r.text(); };

  // Teks tampilan dari elemen yang sudah dirender (punya innerText)
  const uraianFromDoc = doc => {
    const heads = [...doc.querySelectorAll('*')].filter(e =>
      e.childElementCount === 0 && (e.textContent || '').trim().toUpperCase() === 'URAIAN');
    for (const h of heads) {
      let box = h.parentElement;
      for (let i = 0; i < 6 && box; i++, box = box.parentElement) {
        const t = (box.innerText || box.textContent || '');
        const body = t.replace(/^[\s\S]*?URAIAN\s*/i, '').trim();
        if (body.length > 20) return body.split('\n').map(s => s.trim()).filter(Boolean).join('\n');
      }
    }
    return null;
  };

  // Render halaman di iframe tersembunyi (kalau isi halaman dibangun oleh JavaScript)
  const renderInIframe = (url, check, timeout = 20000) => new Promise((resolve, reject) => {
    const f = document.createElement('iframe');
    f.style.cssText = 'position:fixed;left:-9999px;width:1200px;height:900px';
    f.src = url;
    const t0 = Date.now();
    const done = (v, e) => { clearInterval(iv); f.remove(); e ? reject(e) : resolve(v); };
    const iv = setInterval(() => {
      try {
        const d = f.contentDocument;
        const v = d && check(d);
        if (v) return done(v);
      } catch (e) { return done(null, new Error('iframe diblok: ' + e.message)); }
      if (Date.now() - t0 > timeout) done(null, new Error('timeout render ' + url));
    }, 500);
    document.body.appendChild(f);
  });

  // UUID "latar" = yang ada di halaman lain juga (menu, dll.) -> diabaikan
  const BASE = new Set();
  for (const u of ['/id/kbli', '/id/kbli/konversi/00000']) {
    try { ((await getText(u)).match(UUID) || []).forEach(x => BASE.add(x)); } catch (e) {}
  }
  console.log('UUID latar diabaikan:', BASE.size);
  const result = {}, failed = {}, debug = {};
  for (const [i, code] of CODES.entries()) {
    try {
      // 1) cari UUID detail KBLI 2020 dari halaman konversi
      const konvUrl = `/id/kbli/konversi/${code}`;
      const raw = await getText(konvUrl);
      // UUID yang muncul tepat setelah "detail/" didahulukan, sisanya dikurangi UUID menu (ada di semua halaman)
      const direct = [...raw.matchAll(/detail\\?\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/gi)].map(m => m[1]);
      const others = (raw.match(UUID) || []).filter(u => !BASE.has(u));
      let uuids = [...new Set([...direct, ...others])];
      if (!uuids.length) {
        uuids = await renderInIframe(konvUrl, d => {
          const hs = [...d.querySelectorAll('a[href*="/kbli/detail/"]')].map(a => a.href.match(UUID)?.[0]).filter(Boolean);
          return hs.length ? [...new Set(hs)] : null;
        });
      }
      if (!uuids.length) throw new Error('UUID tidak ketemu');

      // 2) coba UUID satu per satu sampai ketemu halaman detail yang memuat kode ini
      let got = null;
      for (const uuid of uuids.slice(0, 20)) {
        const url = `/id/kbli/detail/${uuid}`;
        const html = await getText(url);
        if (!html.includes(code)) continue;
        let uraian = uraianFromDoc(new DOMParser().parseFromString(html, 'text/html'));
        if (!uraian) uraian = await renderInIframe(url, d => d.body && d.body.innerText.includes(code) && uraianFromDoc(d));
        if (uraian) { got = { uuid, uraian }; break; }
        if (!debug[code]) debug[code] = { uuid, htmlLen: html.length, sekitarURAIAN: html.slice(Math.max(0, html.search(/uraian/i) - 300), html.search(/uraian/i) + 1500) };
        await sleep(300);
      }
      if (!got) {
        if (!debug[code]) debug[code] = { uuidsDitemukan: uuids.slice(0, 20), sekitarKode: raw.slice(Math.max(0, raw.lastIndexOf(code) - 800), raw.lastIndexOf(code) + 1500), konversiLen: raw.length, sekitarDetail: raw.slice(Math.max(0, raw.search(/detail/i) - 300), raw.search(/detail/i) + 1200) };
        throw new Error('URAIAN tidak ketemu');
      }
      result[code] = got;
      console.log(`✅ ${i + 1}/${CODES.length} ${code}`, TEST ? got.uraian : '');
    } catch (e) {
      failed[code] = e.message;
      console.warn(`❌ ${i + 1}/${CODES.length} ${code}: ${e.message}`);
    }
    await sleep(400);
  }

  console.log('Selesai. Berhasil:', Object.keys(result).length, 'Gagal:', Object.keys(failed).length);
  const keepDebug = Object.fromEntries(Object.entries(debug).slice(0, 3));
  const blob = new Blob([JSON.stringify({ result, failed, debug: keepDebug }, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = TEST ? 'kbli_uraian_TEST.json' : '__OUTFILE__'; a.click();
})();

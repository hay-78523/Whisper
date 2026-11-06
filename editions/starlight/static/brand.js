/* Starlight Voice Studio — gan nhan dien len trang goc (khong sua file HTML goc).
   window.EDITION do may chu chen vao: {name, product, tagline, marquee[], credit}. */
(function () {
  var E = window.EDITION || {};
  var LOGO = '/static/edition/logo-dark.png';
  var page = document.body.getAttribute('data-page') || '';

  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  // 1. nen luoi + quang sang + hat nhieu (NEXUS)
  var bd = el('div', 'sl-backdrop');
  bd.setAttribute('aria-hidden', 'true');
  bd.appendChild(el('div', 'sl-grid'));
  bd.appendChild(el('div', 'sl-glow'));
  document.body.insertBefore(bd, document.body.firstChild);

  // 2. logo Starlight thay cho chu WHISPER o thanh tren va trang quan tri
  var brandHTML = '<img src="' + LOGO + '" alt="' + esc(E.name || 'Starlight') + '">' +
    '<span class="sl-product"><b>' + esc(E.product || 'Voice Studio') + '</b>' + esc(E.tagline || '') + '</span>';
  var head = document.querySelector('.top-navbar .header-porsche');
  if (head) {
    var a = el('a', 'sl-brand', brandHTML);
    a.href = '/';
    head.innerHTML = '';
    head.appendChild(a);
  }
  var adminTitle = document.querySelector('.admin-header .admin-title');
  if (adminTitle) {
    var a2 = el('a', 'sl-brand', brandHTML);
    a2.href = '/';
    a2.style.marginBottom = '14px';
    adminTitle.insertBefore(a2, adminTitle.firstChild);
    var sub = adminTitle.querySelector('.sub');
    if (sub) sub.textContent = sub.textContent.replace(/Whisper STT/g, E.name || 'Starlight Voice Studio');
  }

  // 3. trang dang nhap: logo lon + dong chu nho
  var splash = document.querySelector('.splash-title');
  if (splash) {
    var img = el('img', 'sl-login-logo');
    img.src = LOGO;
    img.alt = E.name || 'Starlight';
    splash.parentNode.insertBefore(img, splash);
    splash.textContent = E.product || 'Voice Studio';
    splash.classList.add('sl-sub');
  }

  // 4. dai chu chay ngay duoi thanh tren
  var nav = document.querySelector('.top-navbar');
  if (nav && E.marquee && E.marquee.length) {
    var items = '';
    for (var r = 0; r < 4; r++) {
      for (var i = 0; i < E.marquee.length; i++) {
        items += '<span' + (i === 0 ? ' class="hl"' : '') + '>' + esc(E.marquee[i]) + '</span>';
      }
    }
    var mq = el('div', 'sl-marquee', '<div class="sl-track">' + items + items + '</div>');
    mq.setAttribute('aria-hidden', 'true');
    document.body.appendChild(mq);
    var place = function () {
      mq.style.top = nav.getBoundingClientRect().height + 'px';
    };
    place();
    window.addEventListener('resize', place);
  }

  // 5. danh so muc 01, 02... cho tieu de tung khoi (nhan muc NEXUS)
  var hs = document.querySelectorAll('.card > h2');
  for (var k = 0; k < hs.length; k++) {
    var idx = el('span', 'sl-idx', (k + 1 < 10 ? '0' : '') + (k + 1));
    hs[k].insertBefore(idx, hs[k].firstChild);
  }

  // 6. chu ky o chan trang
  if (!document.querySelector('.splash-content')) {
    var ft = el('div', 'sl-footer',
      '<span><b>' + esc(E.name || 'Starlight Voice Studio') + '</b> · ' + esc(E.owner || 'Starlight Entertainment Center') + '</span>' +
      '<span>' + esc(E.creditLabel || 'Designed & built by') + ' <span class="nx">' + esc(E.credit || 'NEXUS') + '</span></span>');
    var host = document.querySelector('.wrap') || document.body;
    host.appendChild(ft);
  }
})();

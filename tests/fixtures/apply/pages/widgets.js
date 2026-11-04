// Test-double widgets, modelled on the real markup captured 2026-11-04:
// Greenhouse's react-select comboboxes and Ashby's autocomplete and yes/no
// buttons. Enough behaviour for fill.py to be tested offline; not the real
// components.
(function () {
  function setupCombobox(box) {
    var input = box.querySelector('input[role="combobox"]');
    var list = box.querySelector('[role="listbox"]');
    var options = Array.prototype.slice.call(list.querySelectorAll('[role="option"]'));
    var style = box.getAttribute('data-style') || 'ashby';
    function show() {
      var typed = input.value.toLowerCase();
      options.forEach(function (o) {
        o.hidden = typed && o.textContent.toLowerCase().indexOf(typed) < 0;
      });
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
    }
    function hide() { list.hidden = true; input.setAttribute('aria-expanded', 'false'); }
    input.addEventListener('focus', show);
    input.addEventListener('click', show);
    input.addEventListener('input', show);
    input.addEventListener('keydown', function (e) { if (e.key === 'Escape') hide(); });
    options.forEach(function (o) {
      o.addEventListener('click', function () {
        if (style === 'react-select') {
          // react-select: the input empties, a single-value element appears
          // beside the input container, the hidden required input gets the value.
          var valueContainer = input.parentNode.parentNode;
          var ph = valueContainer.querySelector('[id$="-placeholder"]');
          if (ph) ph.remove();
          var sv = valueContainer.querySelector('.single-value');
          if (!sv) { sv = document.createElement('div'); sv.className = 'single-value'; valueContainer.insertBefore(sv, valueContainer.firstChild); }
          sv.textContent = o.textContent;
          input.value = '';
          input.parentNode.setAttribute('data-value', o.getAttribute('data-value') || o.textContent);
          var hidden = box.querySelector('input[aria-hidden="true"]');
          if (hidden) hidden.value = o.getAttribute('data-value') || o.textContent;
        } else {
          input.value = o.textContent;
        }
        box.setAttribute('data-selected', o.textContent);
        hide();
      });
    });
  }
  document.querySelectorAll('[data-combobox]').forEach(setupCombobox);
  document.querySelectorAll('.ashby-application-form-input-yesno').forEach(function (group) {
    var buttons = group.querySelectorAll('button[data-option]');
    buttons.forEach(function (b) {
      b.addEventListener('click', function () {
        buttons.forEach(function (x) { x.setAttribute('aria-pressed', 'false'); });
        b.setAttribute('aria-pressed', 'true');
        var cb = group.querySelector('input[type="checkbox"]');
        if (cb) cb.checked = b.getAttribute('data-option') === 'yes';
      });
    });
  });
  // The one thing fill.py must never do. Counted, never acted on.
  window.__submits = 0;
  document.querySelectorAll('form').forEach(function (f) {
    f.addEventListener('submit', function (e) { e.preventDefault(); window.__submits += 1; });
  });
  document.querySelectorAll('button.ashby-application-form-submit-button').forEach(function (b) {
    b.addEventListener('click', function () { window.__submits += 1; });
  });
  // A single-line box that strips paragraph breaks, as a browser does.
  document.querySelectorAll('input[data-strips-newlines]').forEach(function (i) {
    i.addEventListener('input', function () { i.value = i.value.replace(/\n+/g, ' '); });
  });
})();

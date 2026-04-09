/**
 * Autocomplete-компонент для выбора сотрудника.
 *
 * Использование:
 *   <div class="emp-autocomplete">
 *     <input type="text" class="form-control emp-search" placeholder="Начните вводить ФИО...">
 *     <input type="hidden" name="from_employee_id" class="emp-value">
 *     <div class="emp-dropdown"></div>
 *   </div>
 *
 * data-атрибуты на .emp-autocomplete:
 *   data-name   — имя hidden-поля (по умолчанию берётся из .emp-value)
 *   data-on-select — имя глобальной функции-колбэка при выборе (получает {id, full_name, ...})
 */

(function () {
    let debounce;

    document.addEventListener('input', function (e) {
        if (!e.target.classList.contains('emp-search')) return;
        clearTimeout(debounce);
        const wrap = e.target.closest('.emp-autocomplete');
        const dd = wrap.querySelector('.emp-dropdown');
        const hidden = wrap.querySelector('.emp-value');
        const q = e.target.value.trim();

        // Сбросить выбор при изменении текста
        hidden.value = '';
        wrap.classList.remove('selected');

        if (q.length < 1) { dd.innerHTML = ''; dd.style.display = 'none'; return; }

        debounce = setTimeout(function () {
            var extraParams = wrap.dataset.searchParams || '';
            fetch('/api/employees/search?q=' + encodeURIComponent(q) + (extraParams ? '&' + extraParams : ''))
                .then(function (r) { return r.json(); })
                .then(function (list) {
                    if (!list.length) {
                        dd.innerHTML = '<div class="emp-dd-empty">Не найдено</div>';
                        dd.style.display = 'block';
                        return;
                    }
                    dd.innerHTML = list.map(function (emp) {
                        var inactive = emp.is_active === false;
                        return '<div class="emp-dd-item' + (inactive ? ' emp-dd-inactive' : '') + '" data-id="' + emp.id + '" ' +
                            'data-name="' + emp.full_name + '" ' +
                            'data-position="' + emp.position + '" ' +
                            'data-department="' + emp.department + '">' +
                            '<strong>' + emp.full_name + '</strong>' +
                            (inactive ? ' <span class="emp-dd-dismissed">уволен</span>' : '') +
                            (emp.position ? '<br><span class="emp-dd-sub">' + emp.position + '</span>' : '') +
                            (emp.department ? '<span class="emp-dd-sub"> — ' + emp.department + '</span>' : '') +
                            '</div>';
                    }).join('');
                    dd.style.display = 'block';
                });
        }, 200);
    });

    document.addEventListener('click', function (e) {
        var item = e.target.closest('.emp-dd-item');
        if (item) {
            var wrap = item.closest('.emp-autocomplete');
            var input = wrap.querySelector('.emp-search');
            var hidden = wrap.querySelector('.emp-value');
            var dd = wrap.querySelector('.emp-dropdown');

            hidden.value = item.dataset.id;
            input.value = item.dataset.name;
            wrap.classList.add('selected');
            dd.style.display = 'none';

            // Колбэк
            var cbName = wrap.dataset.onSelect;
            if (cbName && typeof window[cbName] === 'function') {
                window[cbName]({
                    id: parseInt(item.dataset.id),
                    full_name: item.dataset.name,
                    position: item.dataset.position,
                    department: item.dataset.department,
                });
            }
        }

        // Закрыть все дропдауны при клике вне
        document.querySelectorAll('.emp-dropdown').forEach(function (dd) {
            if (!dd.parentElement.contains(e.target)) {
                dd.style.display = 'none';
            }
        });
    });
})();

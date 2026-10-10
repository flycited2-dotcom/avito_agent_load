// Read only rendered ARU catalogue cards through the supported CUA evaluate API.
() => {
  const clean = value => (value || '').trim().replace(/\s+/g, ' ');
  const categoryLinks = selector => {
    const found = new Map();
    for (const a of document.querySelectorAll(selector)) {
      const url = new URL(a.href, document.URL);
      if (url.protocol !== 'https:' || url.hostname !== 'aru.ooo' ||
          url.pathname === '/' || !url.pathname.endsWith('/') || url.search || url.hash) continue;
      const name = clean(a.querySelector('[class$="__name"]')?.textContent || a.textContent);
      if (name) found.set(url.pathname, { id: url.pathname, name, url: url.href });
    }
    return [...found.values()];
  };
  const cards = [...document.querySelectorAll('.v-products-list__item[data-product-id]')];
  return {
    authenticated: [...document.querySelectorAll('a')].some(a => a.textContent.trim() === 'Выйти'),
    url: document.URL,
    title: clean(document.querySelector('h1')?.textContent),
    categories: categoryLinks('.v-catalog-menu__list a[href],.v-catalog-side__list a[href]'),
    children: categoryLinks('.v-catalog-icon__list a[href]'),
    pages: Math.max(1, ...[...document.querySelectorAll('.pagination a')].map(a => Number(new URL(a.href).searchParams.get('page')) || 1)),
    // Preserve every rendered observation, including unavailable rows. The
    // compiler reconciles latest observations before applying stock/price rules.
    items: cards.map(c => {
      const text = s => (c.querySelector(s)?.textContent || '').trim().replace(/\s+/g, ' ');
      const specifications = Object.fromEntries([...c.querySelectorAll('.v-products-list__features-tr')].map(r => [
        (r.querySelector('.v-products-list__features-name')?.textContent || '').trim(),
        (r.querySelector('.v-products-list__features-value')?.textContent || '').trim()
      ]));
      return {
        id: c.getAttribute('data-product-id'), article: text('.v-products-list-card__sku').replace(/^Артикул\s*-\s*/, ''),
        name: text('.v-products-list__name'), url: c.querySelector('.v-products-list__name')?.href || '',
        stock_text: text('.v-products-list-card__stock'), price_text: text('.v-products-list__price'),
        old_price_text: text('.v-products-list__price-old'), specifications, brand: specifications['Бренд'] || '',
        image_urls: [...new Set([...c.querySelectorAll('img')]
          .map(i => i.getAttribute('data-src') || i.getAttribute('src'))
          .filter(Boolean).map(src => new URL(src, document.URL))
          .filter(url => url.protocol === 'https:' && url.hostname === 'aru.ooo')
          .map(url => url.href))],
        description: text('.v-products-list__text')
      };
    })
  };
}

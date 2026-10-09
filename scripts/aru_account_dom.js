// Read only rendered ARU catalogue cards through the supported CUA evaluate API.
() => {
  const cards = [...document.querySelectorAll('.v-products-list__item[data-product-id]')];
  return {
    authenticated: [...document.querySelectorAll('a')].some(a => a.textContent.trim() === 'Выйти'),
    url: document.URL,
    pages: Math.max(1, ...[...document.querySelectorAll('.pagination a')].map(a => Number(new URL(a.href).searchParams.get('page')) || 1)),
    items: cards.slice(0, 30).map(c => {
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
        image_urls: [...c.querySelectorAll('img')].map(i => new URL(i.getAttribute('data-src') || i.getAttribute('src'), document.URL).href),
        description: text('.v-products-list__text')
      };
    })
  };
}

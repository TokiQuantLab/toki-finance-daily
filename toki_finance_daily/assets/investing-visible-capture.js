() => {
  'use strict';
  const visible = node => !!node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden'
    && !node.closest('script,style,template,[hidden],nav,aside,footer,header,[aria-hidden="true"]');
  const text = node => node.innerText.replace(/\s+/g,' ').trim();
  const escape = value => String(value).replace(/[&<>"']/g,c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const tag = (name,value,attrs='') => '<'+name+attrs+'>'+escape(value)+'</'+name+'>';
  const selectors = [...document.querySelectorAll('[data-test="timeframe-selection"]')].filter(visible);
  const headings = [...document.querySelectorAll('h1')].filter(visible);
  if (selectors.length !== 1 || headings.length !== 1) throw new Error('Visible instrument heading or timeframe selector is ambiguous');
  const selector=selectors[0], scope=selector.parentElement;
  const subheadings=[...document.querySelectorAll('h2[data-test="section-sub-title"]')]
    .filter(visible).filter(node=>/技术分析|Technical Analysis/i.test(text(node)));
  if (subheadings.length>1) throw new Error('Visible technical instrument subtitle is ambiguous');
  const currency=[...document.querySelectorAll('[data-test="currency-in-label"]')].filter(visible);
  if (currency.length>1) throw new Error('Visible quotation currency is ambiguous');
  const tabs=[...selector.querySelectorAll('button[role="tab"]')].filter(visible);
  const tabHTML=tabs.map(tab=>{
    const attrs=' role="tab" data-test="'+escape(tab.getAttribute('data-test') || '')+'"'
      +(tab.hasAttribute('aria-selected')?' aria-selected="'+escape(tab.getAttribute('aria-selected'))+'"':'')
      +(tab.classList.contains('!border-inv-blue-500')?' class="!border-inv-blue-500"':'');
    const leaves=[...tab.querySelectorAll('span')].filter(visible)
      .filter(node=>![...node.querySelectorAll('span')].some(visible)).map(text).filter(Boolean);
    const labels=leaves.length ? leaves : tab.innerText.split(/\n+/).map(x=>x.trim()).filter(Boolean);
    const paid=[...tab.querySelectorAll('a[href]')].filter(visible).find(a=>/\/pro\//.test(a.getAttribute('href')));
    const parts=labels.map(label=>tag('span',label)).join('');
    return '<button'+attrs+'>'+(paid?'<a href="'+escape(paid.getAttribute('href'))+'">'+parts+'</a>':parts)+'</button>';
  }).join('');
  const tables=[...scope.querySelectorAll('table')].filter(visible);
  if (tables.length!==4) throw new Error('Expected four visible free technical tables');
  const sections=tables.map(table=>{
    let owner=table.parentElement;
    while(owner && owner!==scope && ![...owner.children].some(n=>['H2','H3'].includes(n.tagName))) owner=owner.parentElement;
    if (!owner || owner===scope) throw new Error('Technical table has no independent visible section');
    const title=[...owner.children].find(n=>['H2','H3'].includes(n.tagName)&&visible(n));
    if (!title) throw new Error('Technical table section title is not visible');
    const ownedTables=[...owner.querySelectorAll('table')].filter(visible);
    if (ownedTables.length!==1 || ownedTables[0]!==table) throw new Error('Technical table section is ambiguous');
    const ownText=[...owner.children].filter(n=>visible(n)&&n!==title&&!n.contains(table))
      .map(text).join(' ');
    const times=[...new Set(ownText.match(/\b\d{1,2}\/\d{1,2}\/\d{4}\s+\d{1,2}:\d{2}\s+GMT\b/g) || [])];
    if (times.length>1) throw new Error('Technical table has conflicting source timestamps');
    const summary=ownText.match(/(?:总结|Summary)\s*:\s*(强力买入|强力卖出|买入|卖出|中性|Strong Buy|Strong Sell|Buy|Sell|Neutral)(?:\s|$)/i);
    const rows=[...table.rows].filter(visible).map(row=>'<tr>'+[...row.children]
      .filter(cell=>['TH','TD'].includes(cell.tagName)&&visible(cell)).map(cell=>tag(cell.tagName.toLowerCase(),text(cell))).join('')+'</tr>').join('');
    return '<div>'+tag('h2',text(title))+(summary?tag('p',summary[0].trim()):'')+(times.length?tag('p',times[0]):'')+'<table>'+rows+'</table></div>';
  }).join('');
  return '<html><body>'+tag('h1',text(headings[0]))
    +(subheadings.length?tag('h2',text(subheadings[0]),' data-test="section-sub-title"'):'')
    +(currency.length?tag('div',text(currency[0]),' data-test="currency-in-label"'):'')
    +'<div><div data-test="timeframe-selection">'+tabHTML+'</div>'+sections+'</div></body></html>';
}

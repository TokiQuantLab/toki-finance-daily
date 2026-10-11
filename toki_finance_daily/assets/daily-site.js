(() => {
  'use strict';
  const edition = JSON.parse(document.getElementById('daily-data').textContent);
  const records = edition.records;
  const byId = new Map(records.map(record => [record.id, record]));
  const $ = id => document.getElementById(id);
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  };
  const number = value => typeof value === 'number' && Number.isFinite(value)
    ? new Intl.NumberFormat('en-US', {maximumFractionDigits:20}).format(value) : String(value ?? '—');
  const scalar = value => value === null || value === undefined || value === '' ? '—'
    : typeof value === 'object' ? '—' : number(value);
  const safeURL = value => {
    if (typeof value !== 'string' || /[\u0000-\u001f]/.test(value)) return '';
    try {
      const url = new URL(value);
      return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';
    } catch (_) { return ''; }
  };
  function sourceLink(record, label = '原文 ↗') {
    const url = safeURL(record.url);
    if (!url) return el('span', '', record.source || label);
    const link = el('a', '', label);
    link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    return link;
  }
  function localLink(path, label, download = false) {
    const link = el('a', 'button quiet', label);
    link.href = path;
    if (download) link.download = '';
    return link;
  }
  function time(value, full = false) {
    if (!value) return '时间未标注';
    if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value + '（仅日期）';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone:'Asia/Shanghai', ...(full ? {year:'numeric'} : {}), month:'2-digit', day:'2-digit',
      hour:'2-digit', minute:'2-digit', hourCycle:'h23'
    }).format(date) + ' 上海时间';
  }
  function day(value, zone = 'Asia/Shanghai') {
    if (/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return value;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    try {
      const p = new Intl.DateTimeFormat('en-US', {timeZone:zone, year:'numeric', month:'2-digit', day:'2-digit'}).formatToParts(date);
      const pick = key => p.find(part => part.type === key).value;
      return pick('year') + '-' + pick('month') + '-' + pick('day');
    } catch (_) { return ''; }
  }
  function evidence(ids) {
    const container = el('div', 'evidence-links');
    [...new Set(ids || [])].forEach(id => {
      const record = byId.get(id);
      if (record) container.append(sourceLink(record, record.source + ' · ' + record.title));
    });
    return container;
  }
  function article(item, isLead, index) {
    const card = el('article', isLead ? 'lead-card' : 'story-card');
    const kicker = el('div', 'article-kicker');
    kicker.append(el('span', '', item.category), el('span', '', isLead ? 'TODAY’S FOCUS' : String(index).padStart(2, '0')));
    card.append(kicker, el('h3', '', item.title));
    const columns = el('div', 'article-columns');
    [['fact','事实'],['analysis','解读']].forEach(([key,label]) => {
      const cell = el('div', key);
      cell.append(el('span','copy-label',label),el('p','',item[key]));
      columns.append(cell);
    });
    const watch = el('div', 'article-watch');
    watch.append(el('b','','观察'),el('span','',item.watch));
    card.append(columns, watch, evidence(item.evidence_ids));
    return card;
  }
  $('edition').textContent = edition.brief.edition;
  $('headline').textContent = edition.brief.headline;
  $('dek').textContent = edition.brief.dek;
  $('edition-date').textContent = edition.date.replaceAll('-', '.');
  $('edition-time').textContent = '本期截至 ' + time(edition.cutoff);
  if (edition.mode === 'demo') $('edition-time').textContent = '离线演示 · ' + $('edition-time').textContent;
  $('editorial').append(article(edition.brief.lead,true,0));
  const storyGrid = el('div','story-grid');
  edition.brief.stories.forEach((item,index) => storyGrid.append(article(item,false,index+1)));
  $('editorial').append(storyGrid);
  edition.brief.watchlist.forEach((item,index) => {
    const card = el('article','scenario');
    card.append(el('small','','SCENARIO ' + String(index+1).padStart(2,'0')),el('h3','',item.title),el('p','',item.detail),evidence(item.evidence_ids));
    $('scenarios').append(card);
  });

  // Every market/official observation is independent of technical ratings.
  records.filter(record => record.kind === 'macro' && record.data.topic !== 'technical').forEach(record => {
    const data = record.data, market = data.topic === 'market';
    const card = el('article','data-card' + (market ? ' market' : ''));
    card.dataset.recordId = record.id;
    card.append(el('h3','',data.symbol_name || data.label || record.title));
    const reading = el('p','data-reading',scalar(data.value));
    reading.append(el('small','',data.unit)); card.append(reading);
    if (market && data.ups_percent !== undefined && data.ups_percent !== null) {
      const sign = Number(data.ups_percent), prefix = sign > 0 ? '+' : '';
      card.append(el('p','data-change ' + (sign > 0 ? 'up' : sign < 0 ? 'down' : ''),prefix + scalar(data.ups_percent) + '%'));
    }
    card.append(el('p','data-period',market ? '供应商报价' : (data.reference_only ? '参考汇率 · ' : '统计期 · ') + scalar(data.period)));
    if (market) card.append(el('p','data-time','报价时点 ' + time(data.provider_at,true)));
    else if (record.published_at) card.append(el('p','data-time','发布 ' + time(record.published_at,true)));
    const source = el('p','data-source'); source.append(sourceLink(record,record.source)); card.append(source);
    $('market-cards').append(card);
  });

  const news = records.filter(record => record.kind === 'news').sort((a,b) =>
    (Date.parse(b.published_at || b.published_date) || 0) - (Date.parse(a.published_at || a.published_date) || 0));
  function options(select, entries, current) {
    select.replaceChildren();
    entries.forEach(([value,label]) => {
      const option = el('option','',label); option.value = value; select.append(option);
    });
    if (entries.some(([value]) => value === current)) select.value = current;
  }
  options($('news-source'),[['','全部来源'],...[...new Set(news.map(record => record.source))].sort().map(value => [value,value])]);
  options($('news-topic'),[['','全部主题'],...[...new Set(news.flatMap(record => record.topics))].sort().map(value => [value,value])]);
  $('news-counter').textContent = news.length + ' 则资讯';
  const newsBatch = 6;
  let newsLimit = newsBatch;
  function resetReaderScroll(id) {
    // Keyboard and momentum scrolls can resume after scrollTo. Preserve the
    // cards and accessible region, but detach their old animation target.
    const previous = $(id), scroller = previous.cloneNode(false);
    scroller.append(...previous.childNodes);
    previous.replaceWith(scroller);
    scroller.scrollTo({top:0,left:0,behavior:'instant'});
  }
  function renderNews() {
    const query = $('news-search').value.trim().toLocaleLowerCase(), source = $('news-source').value, topic = $('news-topic').value;
    const filtered = news.filter(record => (!source || record.source === source) && (!topic || record.topics.includes(topic))
      && (!query || [record.title,record.summary,record.original_source,...record.topics].join(' ').toLocaleLowerCase().includes(query)));
    const scrollTop = $('news-scroll').scrollTop;
    $('news-list').replaceChildren();
    filtered.slice(0,newsLimit).forEach(record => {
      const card = el('article','news-card'); card.dataset.recordId = record.id;
      const meta = el('div','news-meta');
      meta.append(el('span','',record.source),el('span','',record.published_at ? time(record.published_at) : record.published_date ? record.published_date + '（仅日期）' : '未标发布时间'));
      card.append(meta,el('h3','',record.title),el('p','',record.summary));
      if (record.original_source) card.append(el('p','original-source','原媒体：' + record.original_source));
      const bottom = el('div','news-bottom'), tags = el('div','topic-tags');
      record.topics.slice(0,2).forEach(label => tags.append(el('span','topic-tag',label)));
      bottom.append(tags,sourceLink(record)); card.append(bottom); $('news-list').append(card);
    });
    if (!filtered.length) $('news-list').append(el('p','empty-state','没有符合当前筛选的资讯，请换个关键词或来源。'));
    $('news-result').textContent = query || source || topic ? filtered.length + ' 则相关资讯' : '按原始发布时间阅读；同一消息的转载不作为独立确认。';
    const shown = Math.min(filtered.length,newsLimit), remaining = filtered.length-shown;
    const expanded = shown > newsBatch;
    $('news-reader-bar').hidden = !expanded;
    $('news-progress').textContent = '已显示 ' + shown + ' / ' + filtered.length + ' 则';
    ['news-more','news-all','news-collapse'].forEach(id => $(id).setAttribute('aria-expanded',String(expanded)));
    $('news-more').textContent = '继续阅读 ' + Math.min(newsBatch,remaining) + ' 则';
    $('news-all').textContent = '展开全部 ' + filtered.length + ' 则资讯';
    $('news-actions').hidden = $('news-more').hidden = $('news-all').hidden = remaining === 0;
    $('news-scroll').scrollTop = scrollTop;
  }
  function openNews() {
    $('news-reader').scrollIntoView({block:'start',behavior:'instant'});
    $('news-collapse').focus({preventScroll:true});
  }
  ['news-search','news-source','news-topic'].forEach(id => $(id).addEventListener(id === 'news-search' ? 'input' : 'change',() => {
    newsLimit=newsBatch; renderNews(); resetReaderScroll('news-scroll');
  }));
  $('news-more').addEventListener('click',() => {
    const nextIndex = newsLimit;
    newsLimit+=newsBatch; renderNews(); openNews();
    const next = $('news-list').children[nextIndex];
    if (next) $('news-scroll').scrollTop=next.offsetTop-$('news-list').offsetTop;
  });
  $('news-all').addEventListener('click',() => {newsLimit=news.length;renderNews();openNews();});
  $('news-collapse').addEventListener('click',() => {
    newsLimit=newsBatch; renderNews();
    const next = $('news-more').hidden ? $('news-search') : $('news-more');
    $('news-reader').scrollIntoView({block:'start',behavior:'instant'});
    next.focus({preventScroll:true});
    resetReaderScroll('news-scroll');
  });
  renderNews();

  const technicals = records.filter(record => record.data.topic === 'technical');
  const focusedTechnicals = (edition.focus_assets || []).flatMap(asset => (asset.record_ids || []).map(id => byId.get(id)))
    .filter(record => record?.data.topic === 'technical');
  function preferredSnapshot(group) {
    return group.find(record => record.data.selected_timeframe === true && record.data.subtype === 'summary' && record.data.timeframe === '1h')
      || group.find(record => record.data.selected_timeframe === true && record.data.subtype === 'summary');
  }
  const initial = preferredSnapshot(focusedTechnicals)
    || focusedTechnicals[0] || byId.get(edition.default_technical_id) || technicals[0];
  const instrumentMap = new Map();
  [...focusedTechnicals,...technicals].forEach(record => {
    if (!instrumentMap.has(record.instrument)) instrumentMap.set(record.instrument,record.data.instrument_name || record.data.symbol_name || record.data.symbol || record.title);
  });
  options($('technical-asset'),[...instrumentMap.entries()],initial?.instrument);
  const periodKey = record => record.data.timeframe || 'unknown';
  const periodNames = {'unknown':'周期未标','1m':'1 分钟','5m':'5 分钟','15m':'15 分钟','30m':'30 分钟','1h':'1 小时','4h':'4 小时','5h':'5 小时','1D':'日线','1W':'周线','1M':'月线'};
  const periodOrder = ['1m','5m','15m','30m','1h','4h','5h','1D','1W','1M','unknown'];
  function periodLabel(period) {
    return !period || period === 'unknown' ? '周期未标' : (periodNames[period] || period) + '周期';
  }
  function selection() {
    const group = technicals.filter(record => record.instrument === $('technical-asset').value);
    const periods = [...new Set(group.map(periodKey))].sort((a,b) => periodOrder.indexOf(a)-periodOrder.indexOf(b));
    options($('technical-period'),periods.map(value => [value,periodNames[value] || value]),$('technical-period').value || periodKey(preferredSnapshot(group) || initial || {data:{}}));
    const providers = [...new Set(group.map(record => record.data.provider || record.source))];
    options($('technical-provider'),[['','全部提供者'],...providers.map(value => [value,value])],$('technical-provider').value);
    renderTechnicals();
  }
  function signalClass(value) {
    const text = String(value || '').toLowerCase();
    if (/超买|超卖|overbought|oversold/.test(text)) return 'signal-extreme';
    return /买|buy/.test(text) ? 'signal-buy' : /卖|sell/.test(text) ? 'signal-sell' : 'signal-neutral';
  }
  function table(parent, title, headers, rows) {
    if (!rows.length) return;
    parent.append(el('h4','table-caption',title));
    const wrap = el('div','table-wrap'), node = el('table','technical-table'), head = el('thead'), line = el('tr');
    headers.forEach(text => line.append(el('th','',text))); head.append(line); node.append(head);
    const body = el('tbody');
    rows.forEach(row => {
      const line = el('tr');
      row.forEach((value,index) => {
        const cell = el('td',index > 0 ? 'numeric' : '',scalar(value));
        if (index === row.length-1 && typeof value === 'string' && /买|卖|buy|sell|neutral|中性|overbought|oversold/i.test(value)) cell.classList.add(signalClass(value));
        line.append(cell);
      }); body.append(line);
    }); node.append(body); wrap.append(node); parent.append(wrap);
  }
  function technicalTables(card,data) {
    const countLabels = {moving_averages:'移动平均',indicators:'技术指标',oscillators:'震荡指标',summary:'综合',
      buy:'买入',sell:'卖出',neutral:'中性',total:'合计'};
    const countLabel = value => countLabels[value] || value;
    const rows = data.indicators || data.oscillators || (data.subtype === 'indicators' && Array.isArray(data.details) ? data.details : []);
    table(card,'技术指标',['指标','数值','原始信号'],rows.map(item => [item.name,item.value_raw ?? item.value,item.signal ?? item.action ?? item.rating]));
    const averages = data.moving_averages || (data.subtype === 'moving_averages' && Array.isArray(data.details) ? data.details : []);
    const maRows = [];
    averages.forEach(item => {
      if (item.simple || item.exponential) {
        [['simple','SMA'],['exponential','EMA']].forEach(([key,type]) => {
          if (item[key]) maRows.push([item.name,type,item[key].value_raw ?? item[key].raw ?? item[key].value,item[key].signal ?? item[key].action ?? item[key].rating]);
        });
      } else maRows.push([item.name,item.ma_type || '—',item.value_raw ?? item.value,item.signal ?? item.action ?? item.rating]);
    });
    table(card,'移动平均',['长度 / 指标','种类','数值','原始信号'],maRows);
    const pivotRows = [];
    if (Array.isArray(data.pivots)) data.pivots.forEach(item => {
      ['classic','fibonacci','camarilla','woodie','dm'].forEach(method => {
        if (Object.hasOwn(item,method)) pivotRows.push([method,item.name,item.value_raw?.[method] ?? item[method]]);
      });
    });
    if (data.subtype === 'pivot_points' && data.details) {
      (Array.isArray(data.details) ? data.details : [data.details]).forEach(method => {
        Object.entries(method.levels || {}).forEach(([level,value]) => pivotRows.push([method.method,level,method.raw_levels?.[level] ?? value]));
      });
    }
    table(card,'枢轴点',['方法','价位','数值'],pivotRows);
    if (data.subtype === 'candlestick_patterns' && Array.isArray(data.details)) {
      table(card,'K 线形态',['形态','阶段','方向','可靠性','距今 K 线','形态日期'],data.details.map(item =>
        [item.pattern,item.stage,item.direction,item.reliability,item.bars_ago_raw ?? item.bars_ago,item.pattern_date_raw ?? item.pattern_date]));
    }
    if (data.subtype === 'indicators' && data.details && !Array.isArray(data.details)) {
      const detail = data.details;
      table(card,'公开指标摘要',['字段','原始值'],[['最新价',detail['最新价'] ?? detail.last_price],['ATR(14)',detail['ATR(14)']],['指标总结',detail['指标总结']]]
        .filter(([,value]) => value !== undefined && value !== null && value !== ''));
      table(card,'指标数量',['原始分类','数量'],Object.entries(detail.counts || {}).map(([label,value]) => [countLabel(label),value]));
    }
    const countRows = [];
    Object.entries(data.counts || {}).forEach(([group,values]) => {
      if (values && typeof values === 'object') Object.entries(values).forEach(([label,value]) => countRows.push([countLabel(group),countLabel(label),value]));
      else countRows.push(['指标',countLabel(group),values]);
    });
    table(card,'信号数量',['分组','原始分类','数量'],countRows);
  }
  function renderTechnicals() {
    const matches = technicals.filter(record => record.instrument === $('technical-asset').value && periodKey(record) === $('technical-period').value
      && (!$('technical-provider').value || (record.data.provider || record.source) === $('technical-provider').value));
    $('technical-list').replaceChildren();
    matches.forEach(record => {
      const data = record.data, card = el('article','technical-record');
      card.dataset.recordId = record.id; card.dataset.timeframe = periodKey(record);
      const header = el('div','technical-record-header'), names = el('div');
      names.append(el('h3','',data.instrument_header || data.symbol_name || data.instrument_name || record.title));
      const parts = [periodNames[periodKey(record)] || data.timeframe_label || data.timeframe,
        data.symbol ? '代码 ' + data.symbol : '',data.contract ? '合约 ' + data.contract : '',data.market];
      names.append(el('p','technical-meta',parts.filter(Boolean).join(' · ')));
      header.append(names,el('span','source-pill',record.source)); card.append(header);
      const ratings = [['rating','综合'],['indicator_rating','技术指标'],['moving_average_rating','移动平均']];
      const ratingGrid = el('div','rating-grid');
      ratings.forEach(([key,label]) => {
        const value = data[key] ?? ((key === 'rating' && (!data.subtype || data.subtype === 'summary')) ? data.value : null);
        if (value !== null && value !== undefined && value !== '') {
          const cell = el('div','rating-cell'); cell.append(el('small','',label),el('strong',signalClass(value),value));
          if (data[key + '_raw'] && data[key + '_raw'] !== value) cell.append(el('small','',data[key + '_raw']));
          ratingGrid.append(cell);
        }
      });
      if (ratingGrid.childElementCount) card.append(ratingGrid);
      technicalTables(card,data);
      card.append(el('p','technical-time',data.provider_at ? '来源更新 ' + time(data.provider_at,true) : '观察时点 ' + time(record.observed_at,true) + ' · 来源更新时间未确认'));
      const source = el('p','technical-source'); source.append(sourceLink(record,'查看 ' + (data.provider || record.source) + ' 原页 ↗')); card.append(source);
      $('technical-list').append(card);
    });
    if (!matches.length) $('technical-list').append(el('p','empty-state','该选择下没有本期技术表格。'));
  }
  $('technical-asset').addEventListener('change',() => { $('technical-period').value='';selection(); });
  $('technical-period').addEventListener('change',renderTechnicals);
  $('technical-provider').addEventListener('change',renderTechnicals);
  selection();

  // Only explicitly bound records enter the asset and rating overview.
  const focusAssets = (edition.focus_assets || []).map(asset => ({...asset,
    records:(asset.record_ids || []).map(id => byId.get(id)).filter(record => record?.data.topic === 'technical')
  }));
  function focusPeriod(group) {
    const selected = preferredSnapshot(group) || group.find(record => record.data.selected_timeframe === true && record.data.timeframe);
    if (selected) return selected.data.timeframe;
    const detailPeriods = [...new Set(group.filter(record => ['indicators','moving_averages','pivot_points'].includes(record.data.subtype))
      .map(record => record.data.timeframe).filter(Boolean))];
    return detailPeriods.length === 1 ? detailPeriods[0] : null;
  }
  function tableStamp(record) {
    if (!record) return '—';
    return record.data.provider_at ? '来源更新 ' + time(record.data.provider_at,true)
      : '观察时点 ' + time(record.observed_at,true) + ' · 来源更新时间未确认';
  }
  function instrumentIdentity(data) {
    return data.instrument_header || data.symbol_name || data.symbol || data.instrument_name || '—';
  }
  function renderFocusRatings(columns, cycles) {
    const parent = $('focus-ratings');
    parent.append(el('h3','focus-table-title','多周期 · 综合评级'));
    const wrap = el('div','focus-matrix-wrap'), node = el('table','focus-matrix');
    node.dataset.group = 'ratings';
    const head = el('thead'), header = el('tr');
    header.append(el('th','focus-row-label','周期'));
    columns.forEach(({asset,record}) => {
      const cell = el('th','focus-column-heading'); cell.dataset.focusKey = asset.key;
      cell.append(el('strong','',asset.label));
      if (record) {
        const data = record.data;
        cell.append(el('span','focus-actual-instrument',instrumentIdentity(data)));
        const parts = [data.contract ? '合约 ' + data.contract : data.symbol ? '代码 ' + data.symbol : '',data.market];
        cell.append(el('small','',parts.filter(Boolean).join(' · ')),
          el('small','',data.provider || record.source),el('small','focus-table-time',tableStamp(record)));
      } else cell.append(el('small','','—'));
      header.append(cell);
    });
    head.append(header); node.append(head);
    const body = el('tbody');
    cycles.forEach(key => {
      const line = el('tr'); line.dataset.rowKey = key;
      const rowHead = el('th','focus-row-label',periodNames[key] || key); rowHead.scope = 'row'; line.append(rowHead);
      columns.forEach(({asset,rows,record}) => {
        const item = rows.find(row => row.key === key), cell = el('td','focus-value-cell');
        cell.dataset.focusKey = asset.key;
        if (item) {
          cell.dataset.recordId = item.record.id;
          cell.dataset.timeframe = item.record.data.timeframe || '';
          cell.title = tableStamp(item.record);
          const value = el('strong',signalClass(item.value),scalar(item.value));
          cell.append(value);
          if (item.record.data.rating_raw && item.record.data.rating_raw !== item.value)
            cell.append(el('span','focus-signal',item.record.data.rating_raw));
          if (tableStamp(item.record) !== tableStamp(record))
            cell.append(el('small','focus-cell-time',tableStamp(item.record)));
        } else cell.append(el('span','focus-missing','—'));
        line.append(cell);
      });
      body.append(line);
    });
    node.append(body); wrap.append(node); parent.append(wrap);
  }
  function renderFocus() {
    if (!focusAssets.length) return;
    $('focus').hidden = false; $('focus-nav').hidden = false; document.body.classList.add('has-focus');
    $('hero-primary').href = '#focus'; $('hero-primary').replaceChildren(document.createTextNode('查看交易品种'),el('span','','↗'));
    focusAssets.forEach(asset => {
      const selectedPeriod = focusPeriod(asset.records);
      const summary = asset.records.find(record => record.data.timeframe === selectedPeriod && (!record.data.subtype || record.data.subtype === 'summary'));
      const representative = summary || asset.records[0], card = el('article','focus-card');
      card.dataset.focusKey = asset.key;
      card.append(el('p','eyebrow','TRADING ASSET'),el('h3','',asset.label));
      if (!representative) {
        card.append(el('strong','focus-card-rating','—'),el('p','focus-contract','—'),el('p','focus-card-meta','本期未提供品种数据'));
      } else {
        const data = representative.data;
        const instrument = el('div','focus-contract');
        instrument.append(el('small','focus-target-label','本期技术标的'),el('strong','focus-actual-instrument',instrumentIdentity(data)));
        const identity = [data.market,data.contract ? '合约 ' + data.contract : data.symbol ? '代码 ' + data.symbol : ''];
        instrument.append(el('small','focus-contract-code',identity.filter(Boolean).join(' · ')));
        card.append(instrument);
        const rating = summary?.data.rating ?? summary?.data.value;
        card.append(el('strong','focus-card-rating ' + signalClass(rating),scalar(rating)));
        card.append(el('p','focus-card-period',selectedPeriod ? periodLabel(selectedPeriod) : '明细周期未标注'));
        const meta = el('p','focus-card-meta');
        meta.append(sourceLink(representative,data.provider || representative.source),document.createTextNode(data.quote_currency ? ' · ' + data.quote_currency : ''));
        card.append(meta,el('p','focus-card-time',tableStamp(summary || representative)));
      }
      $('focus-cards').append(card);
    });
    const summaryColumns = focusAssets.map(asset => {
      const summaries = asset.records.filter(record => !record.data.subtype || record.data.subtype === 'summary');
      return {asset,record:summaries[0],rows:summaries.map(record => ({key:record.data.timeframe,label:periodNames[record.data.timeframe] || record.data.timeframe,
        value:record.data.rating ?? record.data.value,record}))};
    });
    const cycles = [...new Set(summaryColumns.flatMap(column => column.rows.map(row => row.key)).filter(Boolean))]
      .sort((a,b) => periodOrder.indexOf(a)-periodOrder.indexOf(b));
    renderFocusRatings(summaryColumns,cycles);
  }
  renderFocus();

  const calendars = records.filter(record => record.kind === 'calendar');
  const calendarBatch = 12;
  let calendarLimit = calendarBatch;
  const calendarNotes = new Map();
  edition.brief.calendar.forEach(item => item.evidence_ids.forEach(id => calendarNotes.set(id,item.note)));
  function calendarState(record) {
    if (record.data.calendar_scope === 'past') return 'past';
    if (record.data.calendar_scope === 'upcoming') return 'future';
    const at = record.data.event_at;
    if (/^\d{4}-\d{2}-\d{2}$/.test(at || '')) return at < day(edition.cutoff,record.data.provider_timezone || 'Asia/Shanghai') ? 'past' : 'future';
    return Number.isFinite(Date.parse(at)) && Date.parse(at) < Date.parse(edition.cutoff) ? 'past' : 'future';
  }
  function renderCalendar() {
    const scrollTop=$('calendar-scroll').scrollTop;
    const status=$('calendar-status').value, from=$('calendar-from').value, to=$('calendar-to').value;
    const groups=new Map();
    const selected=calendars.filter(record => {
      const date=day(record.data.event_at,record.data.provider_timezone || 'Asia/Shanghai');
      return (!status || status === 'all' || calendarState(record) === status) && (!from || date >= from) && (!to || date <= to);
    }).sort((a,b) => String(a.data.event_at).localeCompare(String(b.data.event_at)));
    selected.slice(0,calendarLimit).forEach(record => {
      const key=day(record.data.event_at,record.data.provider_timezone || 'Asia/Shanghai') || '日期未标';
      if (!groups.has(key)) groups.set(key,[]); groups.get(key).push(record);
    });
    $('calendar-list').replaceChildren();
    groups.forEach((group,date) => {
      const section=el('section','calendar-day'); section.append(el('h3','',date));
      const wrap=el('div','table-wrap'), node=el('table','calendar-table'), head=el('thead'), row=el('tr');
      ['时间','地区 / 币种','重要性','事件','前值','预期','实际'].forEach(label => row.append(el('th','',label))); head.append(row); node.append(head);
      const body=el('tbody');
      group.forEach(record => {
        const data=record.data, row=el('tr'); row.dataset.recordId=record.id; row.dataset.eventState=calendarState(record);
        const clock=el('td','calendar-clock');
        if (/^\d{4}-\d{2}-\d{2}$/.test(data.event_at || '')) clock.append(el('span','','仅日期'),el('small','',data.provider_timezone || '来源当地日期'));
        else {
          const instant=new Date(data.event_at);
          clock.append(el('span','',Number.isNaN(instant.getTime()) ? scalar(data.event_at) : new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(instant)),el('small','','上海时间'));
        }
        const region=[...new Set([data.region || data.country,data.currency].filter(Boolean))].join(' / ') || '—';
        row.append(clock,el('td','',region),el('td','',scalar(data.star ?? data.importance)));
        const event=el('td','calendar-event-cell'); event.append(el('strong','',record.title));
        const actual=data.actual, published=actual !== null && actual !== undefined && actual !== '';
        event.append(el('small','event-state',calendarState(record)==='future' ? '即将发布' : published ? '已公布' : '日程已过'));
        const note=calendarNotes.get(record.id) || data.schedule_note;
        if (note) event.append(el('p','',note));
        if (data.affect_txt || data.impact) event.append(el('p','',data.affect_txt || data.impact));
        const source=el('p','calendar-source'); source.append(sourceLink(record,record.source)); event.append(source); row.append(event);
        row.append(el('td','calendar-value',scalar(data.previous)),el('td','calendar-value',scalar(data.forecast ?? data.consensus)));
        const result=el('td','calendar-value',scalar(actual));
        if (data.revised !== null && data.revised !== undefined && data.revised !== '') result.append(el('small','','修正 ' + scalar(data.revised)));
        row.append(result); body.append(row);
      });node.append(body);wrap.append(node);section.append(wrap);$('calendar-list').append(section);
    });
    if (!selected.length) $('calendar-list').append(el('p','empty-state','当前日期与日程筛选下没有事件。'));
    const shown=Math.min(selected.length,calendarLimit), remaining=selected.length-shown;
    const expanded=shown>calendarBatch;
    $('calendar-result').textContent='共 ' + selected.length + ' 项日程 · 按日期阅读';
    $('calendar-reader-bar').hidden=!expanded;
    $('calendar-progress').textContent='已显示 ' + shown + ' / ' + selected.length + ' 项';
    ['calendar-more','calendar-all','calendar-collapse'].forEach(id => $(id).setAttribute('aria-expanded',String(expanded)));
    $('calendar-more').textContent='继续查看 ' + Math.min(calendarBatch,remaining) + ' 项';
    $('calendar-all').textContent='展开全部 ' + selected.length + ' 项日程';
    $('calendar-actions').hidden=$('calendar-more').hidden=$('calendar-all').hidden=remaining===0;
    $('calendar-scroll').scrollTop=scrollTop;
  }
  function openCalendar() {
    $('calendar-reader').scrollIntoView({block:'start',behavior:'instant'});
    $('calendar-collapse').focus({preventScroll:true});
  }
  ['calendar-status','calendar-from','calendar-to'].forEach(id => $(id).addEventListener('change',() => {
    calendarLimit=calendarBatch;renderCalendar();resetReaderScroll('calendar-scroll');
  }));
  $('calendar-more').addEventListener('click',() => {
    const nextIndex=calendarLimit;
    calendarLimit+=calendarBatch;renderCalendar();openCalendar();
    const next=$('calendar-list').querySelectorAll('tbody tr')[nextIndex], scroller=$('calendar-scroll');
    if (next) scroller.scrollTop+=next.getBoundingClientRect().top-scroller.getBoundingClientRect().top;
  });
  $('calendar-all').addEventListener('click',() => {calendarLimit=calendars.length;renderCalendar();openCalendar();});
  $('calendar-collapse').addEventListener('click',() => {
    calendarLimit=calendarBatch;renderCalendar();
    const next=$('calendar-more').hidden ? $('calendar-status') : $('calendar-more');
    $('calendar-reader').scrollIntoView({block:'start',behavior:'instant'});
    next.focus({preventScroll:true});resetReaderScroll('calendar-scroll');
  });
  renderCalendar();

  if (edition.assets.online) {
    const frame=el('iframe','online-frame');frame.id='online-workbench';frame.src='technical.html?embedded=1';frame.title='Toki 在线技术与财经日历观察台';frame.loading='lazy';
    $('online-container').append(frame);
    window.addEventListener('message',event => {
      if (event.source !== frame.contentWindow || !event.data || event.data.type !== 'toki-workbench-height') return;
      const height=event.data.height;
      if (typeof height !== 'number' || !Number.isFinite(height) || height <= 0) return;
      frame.style.height=Math.max(300,Math.min(12000,Math.ceil(height))) + 'px';
    });
  } else { $('online').hidden=true;$('online-nav').hidden=true; }
  edition.assets.downloads.forEach(item => $('downloads').append(localLink(item.path,item.label + ' ↓',!item.path.endsWith('.html'))));
  $('downloads').append(localLink('public-data.json','公开财经数据 ↓',true));
})();

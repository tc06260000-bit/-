/* Share Link Guard local reply diagnostics — MIT.
 * Pure string comparisons: no network, model/API calls, or execution of source data.
 * These checks cannot establish the truth of every claim or prove a full reading.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.ShareLinkGuardDiagnostics = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const MAX_REPLY_CHARS = 200000;
  const MAX_SOURCE_CHARS = 12 * 1024 * 1024;
  const MAX_MESSAGES = 25000;
  const LIMITATION = '仅核对消息编号、明确条数和可匹配引文；未发现机械问题不代表没有事实错误或幻觉，也不能证明已读完全文。';
  const ALNUM = /[\p{L}\p{N}]/u;
  const substantialLength = text => (text.match(/[\p{L}\p{N}]/gu) || []).length;
  const strongQuote = text => substantialLength(text) >= 8 &&
    (/[\u3400-\u9fff]/.test(text) || normalize(text).split(/\s+/).filter(word => /[\p{L}\p{N}]/u.test(word)).length >= 2);
  const excerpt = text => text.length > 180 ? text.slice(0, 177) + '…' : text;

  function normalize(text) {
    return text.normalize('NFKC').replace(/[\u200b-\u200d\ufeff]/g, '')
      .replace(/\*\*([^*\n]+)\*\*/g, '$1').replace(/`([^`\n]+)`/g, '$1')
      .replace(/\s+/g, ' ').trim();
  }

  function codeSpans(text) {
    return Array.from(text.matchAll(/```[\s\S]*?(?:```|$)|`[^`\n]*`/g), match => ({ start: match.index, end: match.index + match[0].length }));
  }

  function inside(position, spans) {
    let low = 0, high = spans.length;
    while (low < high) {
      const middle = (low + high) >>> 1;
      if (spans[middle].start <= position) low = middle + 1;
      else high = middle;
    }
    return low > 0 && position < spans[low - 1].end;
  }

  function quoteSpans(text, code) {
    const pairs = { '“': '”', '「': '」', '『': '』', '"': '"', "'": "'" };
    const result = [];
    for (let start = 0; start < text.length; start++) {
      const opening = text[start], closing = pairs[opening];
      if (!closing || inside(start, code)) continue;
      if (opening === "'" && (start > 0 && ALNUM.test(text[start - 1]) || /\s/.test(text[start + 1] || ' '))) continue;
      let end = start + 1;
      for (; end < text.length && end - start <= 20000; end++) {
        if (text[end] === '\\' && (opening === '"' || opening === "'")) { end++; continue; }
        if (text[end] === closing) break;
      }
      if (end >= text.length || end - start > 20000) continue;
      if (opening === "'" && ALNUM.test(text[end + 1] || '')) continue;
      result.push({ start, end: end + 1, text: text.slice(start + 1, end) });
      start = end;
    }
    return result;
  }

  function maskSpans(text, spans) {
    if (!spans.length) return text;
    const chars = text.split('');
    for (const { start, end } of spans) {
      for (let i = start; i < end; i++) if (chars[i] !== '\n') chars[i] = ' ';
    }
    return chars.join('');
  }

  function citations(text, quotes, code) {
    const result = [];
    for (const match of text.matchAll(/\[[^\]\r\n]{1,180}\]|【[^】\r\n]{1,180}】/g)) {
      if (inside(match.index, quotes) || inside(match.index, code)) continue;
      const ids = Array.from(match[0].matchAll(/\bT\d{1,8}\b/gi), item => item[0].toUpperCase());
      if (ids.length) result.push({ start: match.index, end: match.index + match[0].length, ids });
    }
    return result;
  }

  function clusters(citations, text) {
    const result = [];
    for (const citation of citations) {
      const previous = result[result.length - 1];
      if (previous && citation.start - previous.end < 24 && /^[\s,，;；、&和及/]*$/.test(text.slice(previous.end, citation.start))) {
        previous.end = citation.end;
        previous.ids.push(...citation.ids);
      } else result.push({ ...citation, ids: [...citation.ids] });
    }
    return result;
  }

  const QUOTE_CUE = /原文|原句|原话|引用|写道|写着|明确说|说的是|(?:\b(?:quote[ds]?|verbatim|says?|states?|reads?|wording|original\s+text)\b)/i;
  const PARAPHRASE = /大意|意译|转述|改写|概括|换句话说|非逐字|并非原句|\b(?:paraphras\w*|roughly|summari[sz]\w*|approximately|in other words|not verbatim)\b/i;
  function associatedIds(quote, groups, text) {
    const nearby = [];
    let low = 0, high = groups.length;
    while (low < high) {
      const middle = (low + high) >>> 1;
      if (groups[middle].end < quote.start - 100) low = middle + 1;
      else high = middle;
    }
    for (let index = low; index < groups.length && groups[index].start <= quote.end + 100; index++) {
      const group = groups[index];
      let gap;
      if (group.end <= quote.start && quote.start - group.end <= 100) gap = text.slice(group.end, quote.start);
      else if (group.start >= quote.end && group.start - quote.end <= 100) gap = text.slice(quote.end, group.start);
      else continue;
      if (/^[\s:：,，;；.。!！?？—\-()（）]*$/.test(gap) || (!/[。！？!?\n]/.test(gap) && QUOTE_CUE.test(gap))) nearby.push(...group.ids);
    }
    return [...new Set(nearby)];
  }

  function quoteMatches(quote, normalizedSource) {
    const normalized = normalize(quote);
    if (!normalized) return false;
    if (normalizedSource.includes(normalized)) return true;
    // Explicit ellipses may omit source text. Check the remaining fragments in
    // order within one cited message instead of demanding a contiguous match.
    if (!/\.{3,}|…/.test(normalized)) return false;
    const fragments = normalized.split(/\s*(?:\[(?:\.{3,}|…+)\]|\.{3,}|…+)\s*/).filter(Boolean);
    if (!fragments.length || fragments.every(fragment => substantialLength(fragment) < 3)) return false;
    let offset = 0;
    for (const fragment of fragments) {
      const found = normalizedSource.indexOf(fragment, offset);
      if (found < 0) return false;
      offset = found + fragment.length;
    }
    return true;
  }

  const CHINESE_DIGITS = { '零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9 };
  const ENGLISH_NUMBERS = { zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9,
    ten: 10, eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18, nineteen: 19, twenty: 20 };
  function numberValue(token) {
    if (/^\d+$/.test(token)) return Number(token);
    if (Object.prototype.hasOwnProperty.call(ENGLISH_NUMBERS, token.toLowerCase())) return ENGLISH_NUMBERS[token.toLowerCase()];
    let result = 0, section = 0, digit = 0;
    for (const char of token) {
      if (Object.prototype.hasOwnProperty.call(CHINESE_DIGITS, char)) digit = CHINESE_DIGITS[char];
      else if (char === '万') { result += (section + digit || 1) * 10000; section = digit = 0; }
      else { section += (digit || 1) * ({ '十': 10, '百': 100, '千': 1000 }[char] || 0); digit = 0; }
    }
    return result + section + digit;
  }

  function sentenceAt(text, start, end) {
    let left = start, right = end;
    while (left > Math.max(0, start - 250) && !/[。！？!?\n]/.test(text[left - 1])) left--;
    while (right < Math.min(text.length, end + 250) && !/[。！？!?\n]/.test(text[right])) right++;
    return text.slice(left, right);
  }
  const ATTRIBUTION = /原(?:文|聊天|对话|记录)(?:中|里|说|写|的助手)|历史(?:对话|消息|记录)(?:中|里|说)|之前(?:的)?(?:助手|回复|回答)(?:说|写|表示)|对方(?:说|写|表示)|\b(?:(?:original|previous|historical|quoted)\s+(?:assistant|message|reply)|(?:assistant|user)\s+(?:said|wrote|reported))\b/i;
  const HYPOTHETICAL = /如果|假设|假如|若有|例如|比如|\b(?:for example|suppose|if there|hypothetical)\b/i;
  function countClaims(masked) {
    const result = [];
    const chineseNumber = '(\\d{1,6}|[零〇一二两三四五六七八九十百千万]{1,8})';
    const patterns = [
      new RegExp('(?:共(?:有|计|收到|读取(?:了)?|读到(?:了)?)?|总共(?:有|收到|读取(?:了)?|读到(?:了)?)?|收到|读到(?:了)?|读取(?:了)?|读取到(?:了)?)\\s*[:：]?\\s*' + chineseNumber + '\\s*条\\s*(?:可见\\s*)?(?:消息|对话(?:消息)?|聊天记录|记录)', 'g'),
      new RegExp('消息(?:总数|数量)(?:为|是|[:：])\\s*' + chineseNumber + '\\s*条?', 'g'),
      /\b(?:I\s+(?:(?:have|already|successfully)\s+)*(?:received|read|loaded)|there\s+(?:are|were)|(?:a\s+)?total\s+of|(?:the\s+)?(?:chat|conversation|source|transcript)\s+(?:contains|has))\s*(?:a\s+total\s+of\s+|all\s+)?(\d{1,6}|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\s+(?:visible\s+)?messages\b/gi
    ];
    for (const pattern of patterns) {
      for (const match of masked.matchAll(pattern)) {
        const sentence = sentenceAt(masked, match.index, match.index + match[0].length);
        if (!ATTRIBUTION.test(sentence) && !HYPOTHETICAL.test(sentence)) result.push({ value: numberValue(match[1]), evidence: match[0], offset: match.index });
      }
    }
    return result;
  }

  function readingClaims(masked) {
    const failure = [], success = [];
    const failurePatterns = [
      /我(?:目前|现在|仍然|这边|暂时|还)?(?:无法|不能|没能|未能|没有|尚未|未)(?:直接|实际|完整|成功|全部)?(?:访问|打开|读取|读到|看到|获取|读过)[^。！？\n]{0,60}/g,
      /我(?:目前|现在|仍然|暂时)?(?:打不开|读不到|看不到)[^。！？\n]{0,60}/g,
      /(?:^|[。！？\n])\s*(?:(?:抱歉|很抱歉|目前|当前|现在|暂时)[，,:：\s]*)*(?:无法|不能|尚未|未能|没有)(?:直接|实际|完整|成功)?(?:访问|打开|读取|读到|看到|获取|读过)[^。！？\n]{0,60}/g,
      /\bI\s+(?:(?:currently|still|actually)\s+)?(?:cannot|can't|can’t|am unable to|haven't|have not|did not|didn't)\s+(?:(?:directly|actually|fully)\s+)?(?:read|access|open|view|retrieve|see)\b[^.!?\n]{0,80}/gi,
      /\bI\s+(?:don't|do not)\s+have\s+access\s+to\b[^.!?\n]{0,80}/gi,
      /(?:^|[.!?\n])\s*(?:Sorry[,;:]?\s*)?(?:Unable to|Cannot)\s+(?:directly\s+)?(?:read|access|open|view)\b[^.!?\n]{0,80}/gi
    ];
    const successPatterns = [
      /我(?:已经|已|刚刚|确实|现在)?(?:完整|全部|逐条|成功|通篇|认真)?(?:读取|读完|读过|看完|阅读)(?:了|过)?[^。！？\n]{0,40}(?:全文|原文|分享|聊天|对话|链接|记录|全部消息|所有消息)/g,
      /我(?:已经|已|刚刚|确实)?(?:完整|全部|逐条|成功)?(?:读完|看完)(?:了)?(?=[，。！？\s]|$)/g,
      /已(?:经)?(?:完整|全部|成功|逐条)?(?:读取|读完|阅读|看完)[^。！？\n]{0,30}(?:全文|原文|分享|聊天|对话|链接|记录)/g,
      /\bI(?:'ve| have| have already| already| just)?\s+(?:(?:fully|completely|successfully|carefully|actually)\s+)?(?:read|reviewed|accessed|opened)\s+(?:(?:the|this|your|entire|full|whole|all)\s+)*(?:conversation|chat|shared?(?: link)?|transcript|source(?: text)?|record|provided text|full text|it all|everything)\b/gi
    ];
    for (const [patterns, target] of [[failurePatterns, failure], [successPatterns, success]]) {
      for (const pattern of patterns) for (const match of masked.matchAll(pattern)) {
        const sentence = sentenceAt(masked, match.index, match.index + match[0].length);
        if (!ATTRIBUTION.test(sentence) && !HYPOTHETICAL.test(sentence)) target.push({ evidence: match[0], offset: match.index });
      }
    }
    return { failure, success };
  }

  function analyzeReply(text, source) {
    const findings = [], findingKeys = new Set();
    let verifiedQuotes = 0, checkedCitations = 0, mechanicalIssue = false, needsReview = false;
    function add(code, severity, message, evidence, extra, key) {
      const identifier = key || code + ':' + (evidence || '');
      if (findingKeys.has(identifier)) return;
      findingKeys.add(identifier);
      findings.push({ code, severity, message, ...(evidence ? { evidence: excerpt(evidence) } : {}), ...(extra || {}) });
    }
    function finish() {
      const status = mechanicalIssue ? 'issues' : needsReview ? 'review' : 'no_mechanical_issue';
      const summary = status === 'issues' ? '发现需要核对的编号、条数或引文问题。' : status === 'review' ? '阅读情况仍需人工核对，不能判定已读完。' : '未发现本地可检测的编号、条数或引文问题；不代表没有幻觉。';
      add('LIMITATION', 'info', LIMITATION);
      return { status, findings, verifiedQuotes, checkedCitations, summary };
    }
    if (typeof text !== 'string' || !text.trim()) {
      needsReview = true;
      add('EMPTY_REPLY', 'info', '回复为空，尚无可核对内容。');
      return finish();
    }
    if (text.length > MAX_REPLY_CHARS) {
      needsReview = true;
      add('CHECK_LIMIT_EXCEEDED', 'warning', '回复超过本地检查预算，未作截断核对，请人工检查。');
      return finish();
    }
    if (!source || !Array.isArray(source.messages) || !source.messages.length || source.messages.length > MAX_MESSAGES) {
      needsReview = true;
      add('SOURCE_UNAVAILABLE', 'warning', '没有有效的已提取来源，无法核对这条回复。');
      return finish();
    }
    const messages = new Map();
    let sourceChars = 0;
    for (const message of source.messages) {
      if (!message || typeof message.id !== 'string' || !/^T\d{4,8}$/.test(message.id) ||
          typeof message.text !== 'string' || messages.has(message.id)) {
        needsReview = true;
        add('SOURCE_INVALID', 'warning', '来源的消息编号或文本不完整，无法可靠核对。');
        return finish();
      }
      sourceChars += message.text.length;
      if (sourceChars > MAX_SOURCE_CHARS) {
        needsReview = true;
        add('CHECK_LIMIT_EXCEEDED', 'warning', '来源超过本地检查预算，未作截断核对，请人工检查。');
        return finish();
      }
      messages.set(message.id, { text: message.text, normalized: normalize(message.text) });
    }
    const code = codeSpans(text), quotes = quoteSpans(text, code);
    const references = citations(text, quotes, code), groups = clusters(references, text);
    for (const reference of references) for (const id of reference.ids) {
      checkedCitations++;
      if (!messages.has(id)) {
        mechanicalIssue = true;
        add('UNKNOWN_CITATION', 'error', '引用编号 [' + id + '] 不在已提取的来源中。', text.slice(reference.start, reference.end),
          { citationId: id, offset: reference.start }, 'UNKNOWN_CITATION:' + id);
      }
    }
    for (const quote of quotes) {
      const ids = associatedIds(quote, groups, text).filter(id => messages.has(id));
      if (!ids.length || substantialLength(quote.text) < 3) continue;
      const surrounding = text.slice(Math.max(0, quote.start - 80), quote.start) + ' ' + text.slice(quote.end, Math.min(text.length, quote.end + 80));
      if (PARAPHRASE.test(surrounding)) continue;
      if (ids.some(id => quoteMatches(quote.text, messages.get(id).normalized))) verifiedQuotes++;
      else if (strongQuote(quote.text)) {
        mechanicalIssue = true;
        add('QUOTE_NOT_MATCHED', 'warning', '紧邻引用编号的引文未在对应消息中匹配；可能有改写、排版或引用对应问题，不能据此认定事实造假。', quote.text,
          { citationIds: ids, offset: quote.start });
      }
    }
    const masked = maskSpans(text, [...code, ...quotes]);
    for (const claim of countClaims(masked)) {
      if (claim.value !== source.messages.length) {
        mechanicalIssue = true;
        add('MESSAGE_COUNT_MISMATCH', 'warning', '回复明确声明 ' + claim.value + ' 条消息，与当前提取来源的 ' + source.messages.length + ' 条不符；请核对其声明的范围。', claim.evidence,
          { claimedCount: claim.value, expectedCount: source.messages.length, offset: claim.offset }, 'MESSAGE_COUNT_MISMATCH:' + claim.value);
      }
    }
    const claims = readingClaims(masked);
    if (claims.failure.length) {
      needsReview = true;
      add('READING_FAILURE_SELF_REPORT', 'warning', '回复自述无法访问或尚未读取原文，可能存在阅读失败；这是模型自述，仍需核对实际读取情况。', claims.failure[0].evidence);
    }
    if (claims.success.length && verifiedQuotes === 0) {
      needsReview = true;
      add('READING_CLAIM_UNVERIFIED', 'warning', '回复声称读过，但没有可匹配原句；仅有编号不足以验证，不能判定已读完。', claims.success[0].evidence);
    }
    return finish();
  }

  return Object.freeze({ analyzeReply });
});

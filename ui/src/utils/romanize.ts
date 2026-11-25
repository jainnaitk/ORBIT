/**
 * Zero-dependency transliterator & script detector for ORBIT Phase B Synced Lyrics.
 * Supports Devanagari (Hindi/Marathi), Gurmukhi (Punjabi), Malayalam, Tamil,
 * Telugu, Kannada, Bengali, Gujarati, Hangul (Korean), Hiragana/Katakana (Japanese),
 * Cyrillic, Greek, and Arabic/Urdu.
 */

// Match only actual non-Latin script letters (excludes symbols like ♪, •, emojis, punctuation)
const NON_LATIN_RE =
  /[\u0370-\u03FF\u0400-\u04FF\u0600-\u06FF\u0900-\u0D7F\u3040-\u30FF\u4E00-\u9FFF\uAC00-\uD7A3]/;

export interface DetectedLyricsLanguage {
  code: string;
  label: string;
}

const SCRIPT_DETECTORS: { code: string; label: string; re: RegExp }[] = [
  { code: 'hi', label: 'Hindi', re: /[\u0900-\u097F]/g },
  { code: 'pa', label: 'Punjabi', re: /[\u0A00-\u0A7F]/g },
  { code: 'ml', label: 'Malayalam', re: /[\u0D00-\u0D7F]/g },
  { code: 'ta', label: 'Tamil', re: /[\u0B80-\u0BFF]/g },
  { code: 'te', label: 'Telugu', re: /[\u0C00-\u0C7F]/g },
  { code: 'kn', label: 'Kannada', re: /[\u0C80-\u0CFF]/g },
  { code: 'bn', label: 'Bengali', re: /[\u0980-\u09FF]/g },
  { code: 'gu', label: 'Gujarati', re: /[\u0A80-\u0AFF]/g },
  { code: 'ko', label: 'Korean', re: /[\uAC00-\uD7A3]/g },
  { code: 'ja', label: 'Japanese', re: /[\u3040-\u30FF]/g },
  { code: 'ur', label: 'Urdu', re: /[\u0600-\u06FF]/g },
  { code: 'ru', label: 'Russian', re: /[\u0400-\u04FF]/g },
  { code: 'el', label: 'Greek', re: /[\u0370-\u03FF]/g },
];

const URDU_SPECIFIC_RE = /[\u0679\u0688\u0691\u06BA\u06D2\u067E\u0686\u0698\u06AF\u06A9]/;

export function hasNonLatinScript(text: string): boolean {
  return NON_LATIN_RE.test(text);
}

/**
 * Detect the dominant transliterable non-Latin script in a lyrics block.
 * Returns null when lyrics are already predominantly Latin/English.
 */
export function detectLyricsLanguage(text: string): DetectedLyricsLanguage | null {
  if (!text || !NON_LATIN_RE.test(text)) return null;

  let best: DetectedLyricsLanguage | null = null;
  let bestCount = 0;

  for (const spec of SCRIPT_DETECTORS) {
    const matches = text.match(spec.re);
    const count = matches ? matches.length : 0;
    if (count > bestCount) {
      bestCount = count;
      best =
        spec.code === 'ur' && !URDU_SPECIFIC_RE.test(text)
          ? { code: 'ar', label: 'Arabic' }
          : { code: spec.code, label: spec.label };
    }
  }

  return bestCount >= 3 ? best : null;
}

// ---------------------------------------------------------------------------
// Generic Indic Abugida Transliterator (shared by Hindi, Malayalam, Tamil, etc.)
// ---------------------------------------------------------------------------
interface IndicScriptSpec {
  vowels: Record<string, string>;
  consonants: Record<string, string>;
  matras: Record<string, string>;
  virama: string;
  nukta?: string;
  nuktaMap?: Record<string, string>;
  anusvara?: string;
  chandrabindu?: string;
  visarga?: string;
  chillu?: Record<string, string>;
  digitZero?: number;
  letterMin: number;
  letterMax: number;
  dropFinalSchwa: boolean;
  labials?: Set<string>;
}

function romanizeIndicWord(word: string, spec: IndicScriptSpec): string {
  let out = '';
  let i = 0;
  let syllableCount = 0;

  const isLetter = (ch: string | undefined): boolean => {
    if (!ch) return false;
    const c = ch.charCodeAt(0);
    return c >= spec.letterMin && c <= spec.letterMax;
  };

  while (i < word.length) {
    const ch = word[i];
    const code = ch.charCodeAt(0);

    if (spec.digitZero !== undefined && code >= spec.digitZero && code <= spec.digitZero + 9) {
      out += String(code - spec.digitZero);
      i++;
      continue;
    }
    if (spec.chillu && spec.chillu[ch]) {
      out += spec.chillu[ch];
      i++;
      continue;
    }
    if (spec.anusvara && ch === spec.anusvara) {
      const next = word[i + 1];
      out += next && spec.labials?.has(next) ? 'm' : 'n';
      i++;
      continue;
    }
    if (spec.chandrabindu && ch === spec.chandrabindu) {
      out += 'n';
      i++;
      continue;
    }
    if (spec.visarga && ch === spec.visarga) {
      out += 'h';
      i++;
      continue;
    }
    if (ch === '\u0964') {
      out += '.';
      i++;
      continue;
    }
    if (ch === '\u0965') {
      out += '..';
      i++;
      continue;
    }

    if (spec.vowels[ch]) {
      out += spec.vowels[ch];
      syllableCount++;
      i++;
      continue;
    }

    if (spec.consonants[ch]) {
      let base = spec.consonants[ch];
      i++;
      if (spec.nukta && word[i] === spec.nukta) {
        base = spec.nuktaMap?.[ch] ?? base;
        i++;
      }

      if (word[i] === spec.virama) {
        out += base;
        i++;
        continue;
      }

      const matra = spec.matras[word[i]];
      if (matra) {
        out += base + matra;
        syllableCount++;
        i++;
        continue;
      }

      const nextCh = word[i];
      const atWordEnd = !isLetter(nextCh);
      let dropSchwa = spec.dropFinalSchwa && atWordEnd && syllableCount > 0;

      // Hindi/Devanagari medial schwa deletion:
      // In VC1aC2V (e.g. मशहूरी -> mashhuri, रखदा -> rakhda, गहरा -> gahra),
      // drop inherent 'a' after C1 when preceded by a vowel and followed by C2 + explicit matra.
      if (
        !dropSchwa &&
        spec.dropFinalSchwa &&
        syllableCount > 0 &&
        nextCh &&
        spec.consonants[nextCh] &&
        /[aeiou]$/.test(out)
      ) {
        let j = i + 1;
        if (spec.nukta && word[j] === spec.nukta) j++;
        const afterNext = word[j];
        if (afterNext && spec.matras[afterNext]) {
          dropSchwa = true;
        }
      }

      out += dropSchwa ? base : `${base}a`;
      syllableCount++;
      continue;
    }

    out += ch;
    i++;
  }

  return out;
}

// Devanagari (Natural Hinglish spelling: single 'a', 'i', 'u' for readable lyrics)
const DEVA_SPEC: IndicScriptSpec = {
  vowels: {
    '\u0905': 'a', '\u0906': 'aa', '\u0907': 'i', '\u0908': 'i',
    '\u0909': 'u', '\u090A': 'u', '\u090B': 'ri', '\u090D': 'e',
    '\u090E': 'e', '\u090F': 'e', '\u0910': 'ai', '\u0911': 'o',
    '\u0912': 'o', '\u0913': 'o', '\u0914': 'au', '\u0972': 'a',
  },
  consonants: {
    '\u0915': 'k', '\u0916': 'kh', '\u0917': 'g', '\u0918': 'gh', '\u0919': 'ng',
    '\u091A': 'ch', '\u091B': 'chh', '\u091C': 'j', '\u091D': 'jh', '\u091E': 'ny',
    '\u091F': 't', '\u0920': 'th', '\u0921': 'd', '\u0922': 'dh', '\u0923': 'n',
    '\u0924': 't', '\u0925': 'th', '\u0926': 'd', '\u0927': 'dh', '\u0928': 'n',
    '\u0929': 'n', '\u092A': 'p', '\u092B': 'ph', '\u092C': 'b', '\u092D': 'bh',
    '\u092E': 'm', '\u092F': 'y', '\u0930': 'r', '\u0931': 'r', '\u0932': 'l',
    '\u0933': 'l', '\u0934': 'l', '\u0935': 'v', '\u0936': 'sh', '\u0937': 'sh',
    '\u0938': 's', '\u0939': 'h',
    '\u0958': 'q', '\u0959': 'kh', '\u095A': 'gh', '\u095B': 'z',
    '\u095C': 'r', '\u095D': 'rh', '\u095E': 'f', '\u095F': 'y',
  },
  nukta: '\u093C',
  nuktaMap: {
    '\u0915': 'q', '\u0916': 'kh', '\u0917': 'gh', '\u091C': 'z',
    '\u0921': 'r', '\u0922': 'rh', '\u092B': 'f', '\u092F': 'y',
  },
  matras: {
    '\u093E': 'a', '\u093F': 'i', '\u0940': 'i', '\u0941': 'u', '\u0942': 'u',
    '\u0943': 'ri', '\u0945': 'e', '\u0946': 'e', '\u0947': 'e', '\u0948': 'ai',
    '\u0949': 'o', '\u094A': 'o', '\u094B': 'o', '\u094C': 'au',
  },
  virama: '\u094D',
  anusvara: '\u0902',
  chandrabindu: '\u0901',
  visarga: '\u0903',
  digitZero: 0x0966,
  letterMin: 0x0904,
  letterMax: 0x0963,
  dropFinalSchwa: true,
  labials: new Set(['\u092A', '\u092B', '\u092C', '\u092D', '\u092E']),
};

// Malayalam (U+0D00 - U+0D7F)
const MALAYALAM_SPEC: IndicScriptSpec = {
  vowels: {
    '\u0D05': 'a', '\u0D06': 'aa', '\u0D07': 'i', '\u0D08': 'ee',
    '\u0D09': 'u', '\u0D0A': 'oo', '\u0D0B': 'ri', '\u0D0E': 'e',
    '\u0D0F': 'e', '\u0D10': 'ai', '\u0D12': 'o', '\u0D13': 'o', '\u0D14': 'au',
  },
  consonants: {
    '\u0D15': 'k', '\u0D16': 'kh', '\u0D17': 'g', '\u0D18': 'gh', '\u0D19': 'ng',
    '\u0D1A': 'ch', '\u0D1B': 'chh', '\u0D1C': 'j', '\u0D1D': 'jh', '\u0D1E': 'nj',
    '\u0D1F': 'd', '\u0D20': 'th', '\u0D21': 'd', '\u0D22': 'dh', '\u0D23': 'n',
    '\u0D24': 'th', '\u0D25': 'th', '\u0D26': 'd', '\u0D27': 'dh', '\u0D28': 'n',
    '\u0D29': 'n', '\u0D2A': 'p', '\u0D2B': 'ph', '\u0D2C': 'b', '\u0D2D': 'bh',
    '\u0D2E': 'm', '\u0D2F': 'y', '\u0D30': 'r', '\u0D31': 'r', '\u0D32': 'l',
    '\u0D33': 'l', '\u0D34': 'zh', '\u0D35': 'v', '\u0D36': 'sh', '\u0D37': 'sh',
    '\u0D38': 's', '\u0D39': 'h', '\u0D3A': 't',
  },
  matras: {
    '\u0D3E': 'a', '\u0D3F': 'i', '\u0D40': 'ee', '\u0D41': 'u', '\u0D42': 'oo',
    '\u0D43': 'ri', '\u0D46': 'e', '\u0D47': 'e', '\u0D48': 'ai',
    '\u0D4A': 'o', '\u0D4B': 'o', '\u0D4C': 'au', '\u0D57': 'au',
  },
  chillu: {
    '\u0D7A': 'n', '\u0D7B': 'n', '\u0D7C': 'r', '\u0D7D': 'l', '\u0D7E': 'l', '\u0D7F': 'k',
  },
  virama: '\u0D4D',
  anusvara: '\u0D02',
  visarga: '\u0D03',
  digitZero: 0x0d66,
  letterMin: 0x0d05,
  letterMax: 0x0d7f,
  dropFinalSchwa: false,
  labials: new Set(['\u0D2A', '\u0D2B', '\u0D2C', '\u0D2D', '\u0D2E']),
};

// Tamil (U+0B80 - U+0BFF)
const TAMIL_SPEC: IndicScriptSpec = {
  vowels: {
    '\u0B85': 'a', '\u0B86': 'aa', '\u0B87': 'i', '\u0B88': 'ee',
    '\u0B89': 'u', '\u0B8A': 'oo', '\u0B8E': 'e', '\u0B8F': 'e',
    '\u0B90': 'ai', '\u0B92': 'o', '\u0B93': 'o', '\u0B94': 'au',
  },
  consonants: {
    '\u0B95': 'k', '\u0B99': 'ng', '\u0B9A': 's', '\u0B9C': 'j', '\u0B9E': 'nj',
    '\u0B9F': 't', '\u0BA3': 'n', '\u0BA4': 'th', '\u0BA8': 'n', '\u0BA9': 'n',
    '\u0BAA': 'p', '\u0BAE': 'm', '\u0BAF': 'y', '\u0BB0': 'r', '\u0BB1': 'r',
    '\u0BB2': 'l', '\u0BB3': 'l', '\u0BB4': 'zh', '\u0BB5': 'v',
    '\u0BB6': 'sh', '\u0BB7': 'sh', '\u0BB8': 's', '\u0BB9': 'h',
  },
  matras: {
    '\u0BBE': 'a', '\u0BBF': 'i', '\u0BC0': 'ee', '\u0BC1': 'u', '\u0BC2': 'oo',
    '\u0BC6': 'e', '\u0BC7': 'e', '\u0BC8': 'ai',
    '\u0BCA': 'o', '\u0BCB': 'o', '\u0BCC': 'au', '\u0BD7': 'au',
  },
  virama: '\u0BCD',
  anusvara: '\u0B82',
  visarga: '\u0B83',
  digitZero: 0x0be6,
  letterMin: 0x0b85,
  letterMax: 0x0bd7,
  dropFinalSchwa: false,
};

// Telugu (U+0C00 - U+0C7F)
const TELUGU_SPEC: IndicScriptSpec = {
  vowels: {
    '\u0C05': 'a', '\u0C06': 'aa', '\u0C07': 'i', '\u0C08': 'ee',
    '\u0C09': 'u', '\u0C0A': 'oo', '\u0C0B': 'ri', '\u0C0E': 'e',
    '\u0C0F': 'e', '\u0C10': 'ai', '\u0C12': 'o', '\u0C13': 'o', '\u0C14': 'au',
  },
  consonants: {
    '\u0C15': 'k', '\u0C16': 'kh', '\u0C17': 'g', '\u0C18': 'gh', '\u0C19': 'ng',
    '\u0C1A': 'ch', '\u0C1B': 'chh', '\u0C1C': 'j', '\u0C1D': 'jh', '\u0C1E': 'ny',
    '\u0C1F': 't', '\u0C20': 'th', '\u0C21': 'd', '\u0C22': 'dh', '\u0C23': 'n',
    '\u0C24': 't', '\u0C25': 'th', '\u0C26': 'd', '\u0C27': 'dh', '\u0C28': 'n',
    '\u0C2A': 'p', '\u0C2B': 'ph', '\u0C2C': 'b', '\u0C2D': 'bh', '\u0C2E': 'm',
    '\u0C2F': 'y', '\u0C30': 'r', '\u0C31': 'r', '\u0C32': 'l', '\u0C33': 'l',
    '\u0C35': 'v', '\u0C36': 'sh', '\u0C37': 'sh', '\u0C38': 's', '\u0C39': 'h',
  },
  matras: {
    '\u0C3E': 'a', '\u0C3F': 'i', '\u0C40': 'ee', '\u0C41': 'u', '\u0C42': 'oo',
    '\u0C43': 'ri', '\u0C46': 'e', '\u0C47': 'e', '\u0C48': 'ai',
    '\u0C4A': 'o', '\u0C4B': 'o', '\u0C4C': 'au',
  },
  virama: '\u0C4D',
  anusvara: '\u0C02',
  visarga: '\u0C03',
  digitZero: 0x0c66,
  letterMin: 0x0c05,
  letterMax: 0x0c4d,
  dropFinalSchwa: false,
};

// Kannada (U+0C80 - U+0CFF)
const KANNADA_SPEC: IndicScriptSpec = {
  vowels: {
    '\u0C85': 'a', '\u0C86': 'aa', '\u0C87': 'i', '\u0C88': 'ee',
    '\u0C89': 'u', '\u0C8A': 'oo', '\u0C8B': 'ri', '\u0C8E': 'e',
    '\u0C8F': 'e', '\u0C90': 'ai', '\u0C92': 'o', '\u0C93': 'o', '\u0C94': 'au',
  },
  consonants: {
    '\u0C95': 'k', '\u0C96': 'kh', '\u0C97': 'g', '\u0C98': 'gh', '\u0C99': 'ng',
    '\u0C9A': 'ch', '\u0C9B': 'chh', '\u0C9C': 'j', '\u0C9D': 'jh', '\u0C9E': 'ny',
    '\u0C9F': 't', '\u0CA0': 'th', '\u0CA1': 'd', '\u0CA2': 'dh', '\u0CA3': 'n',
    '\u0CA4': 't', '\u0CA5': 'th', '\u0CA6': 'd', '\u0CA7': 'dh', '\u0CA8': 'n',
    '\u0CAA': 'p', '\u0CAB': 'ph', '\u0CAC': 'b', '\u0CAD': 'bh', '\u0CAE': 'm',
    '\u0CAF': 'y', '\u0CB0': 'r', '\u0CB1': 'r', '\u0CB2': 'l', '\u0CB3': 'l',
    '\u0CB5': 'v', '\u0CB6': 'sh', '\u0CB7': 'sh', '\u0CB8': 's', '\u0CB9': 'h',
  },
  matras: {
    '\u0CBE': 'a', '\u0CBF': 'i', '\u0CC0': 'ee', '\u0CC1': 'u', '\u0CC2': 'oo',
    '\u0CC3': 'ri', '\u0CC6': 'e', '\u0CC7': 'e', '\u0CC8': 'ai',
    '\u0CCA': 'o', '\u0CCB': 'o', '\u0CCC': 'au',
  },
  virama: '\u0CCD',
  anusvara: '\u0C82',
  visarga: '\u0C83',
  digitZero: 0x0ce6,
  letterMin: 0x0c85,
  letterMax: 0x0ccd,
  dropFinalSchwa: false,
};

// Bengali (U+0980 - U+09FF)
const BENGALI_SPEC: IndicScriptSpec = {
  vowels: {
    '\u0985': 'o', '\u0986': 'a', '\u0987': 'i', '\u0988': 'i',
    '\u0989': 'u', '\u098A': 'u', '\u098B': 'ri', '\u098F': 'e',
    '\u0990': 'oi', '\u0993': 'o', '\u0994': 'ou',
  },
  consonants: {
    '\u0995': 'k', '\u0996': 'kh', '\u0997': 'g', '\u0998': 'gh', '\u0999': 'ng',
    '\u099A': 'ch', '\u099B': 'chh', '\u099C': 'j', '\u099D': 'jh', '\u099E': 'ny',
    '\u099F': 't', '\u09A0': 'th', '\u09A1': 'd', '\u09A2': 'dh', '\u09A3': 'n',
    '\u09A4': 't', '\u09A5': 'th', '\u09A6': 'd', '\u09A7': 'dh', '\u09A8': 'n',
    '\u09AA': 'p', '\u09AB': 'ph', '\u09AC': 'b', '\u09AD': 'bh', '\u09AE': 'm',
    '\u09AF': 'j', '\u09B0': 'r', '\u09B2': 'l', '\u09B6': 'sh', '\u09B7': 'sh',
    '\u09B8': 's', '\u09B9': 'h', '\u09DC': 'r', '\u09DD': 'rh', '\u09DF': 'y',
  },
  matras: {
    '\u09BE': 'a', '\u09BF': 'i', '\u09C0': 'i', '\u09C1': 'u', '\u09C2': 'u',
    '\u09C3': 'ri', '\u09C7': 'e', '\u09C8': 'oi', '\u09CB': 'o', '\u09CC': 'ou',
  },
  virama: '\u09CD',
  anusvara: '\u0982',
  chandrabindu: '\u0981',
  visarga: '\u0983',
  digitZero: 0x09e6,
  letterMin: 0x0985,
  letterMax: 0x09df,
  dropFinalSchwa: true,
};

// Gujarati (U+0A80 - U+0AFF)
const GUJARATI_SPEC: IndicScriptSpec = {
  vowels: {
    '\u0A85': 'a', '\u0A86': 'aa', '\u0A87': 'i', '\u0A88': 'i',
    '\u0A89': 'u', '\u0A8A': 'u', '\u0A8B': 'ri', '\u0A8F': 'e',
    '\u0A90': 'ai', '\u0A93': 'o', '\u0A94': 'au',
  },
  consonants: {
    '\u0A95': 'k', '\u0A96': 'kh', '\u0A97': 'g', '\u0A98': 'gh', '\u0A99': 'ng',
    '\u0A9A': 'ch', '\u0A9B': 'chh', '\u0A9C': 'j', '\u0A9D': 'jh', '\u0A9E': 'ny',
    '\u0A9F': 't', '\u0AA0': 'th', '\u0AA1': 'd', '\u0AA2': 'dh', '\u0AA3': 'n',
    '\u0AA4': 't', '\u0AA5': 'th', '\u0AA6': 'd', '\u0AA7': 'dh', '\u0AA8': 'n',
    '\u0AAA': 'p', '\u0AAB': 'ph', '\u0AAC': 'b', '\u0AAD': 'bh', '\u0AAE': 'm',
    '\u0AAF': 'y', '\u0AB0': 'r', '\u0AB2': 'l', '\u0AB3': 'l', '\u0AB5': 'v',
    '\u0AB6': 'sh', '\u0AB7': 'sh', '\u0AB8': 's', '\u0AB9': 'h',
  },
  matras: {
    '\u0ABE': 'a', '\u0ABF': 'i', '\u0AC0': 'i', '\u0AC1': 'u', '\u0AC2': 'u',
    '\u0AC3': 'ri', '\u0AC7': 'e', '\u0AC8': 'ai', '\u0ACB': 'o', '\u0ACC': 'au',
  },
  virama: '\u0ACD',
  anusvara: '\u0A82',
  chandrabindu: '\u0A81',
  visarga: '\u0A83',
  digitZero: 0x0ae6,
  letterMin: 0x0a85,
  letterMax: 0x0acd,
  dropFinalSchwa: true,
};

// ---------------------------------------------------------------------------
// Gurmukhi / Punjabi (U+0A00 - U+0A7F)
// ---------------------------------------------------------------------------
const GURU_VOWELS: Record<string, string> = {
  '\u0A05': 'a', '\u0A06': 'aa', '\u0A07': 'i', '\u0A08': 'i',
  '\u0A09': 'u', '\u0A0A': 'u', '\u0A0F': 'e', '\u0A10': 'ai',
  '\u0A13': 'o', '\u0A14': 'au',
};

const GURU_CONSONANTS: Record<string, string> = {
  '\u0A15': 'k', '\u0A16': 'kh', '\u0A17': 'g', '\u0A18': 'gh', '\u0A19': 'ng',
  '\u0A1A': 'ch', '\u0A1B': 'chh', '\u0A1C': 'j', '\u0A1D': 'jh', '\u0A1E': 'ny',
  '\u0A1F': 't', '\u0A20': 'th', '\u0A21': 'd', '\u0A22': 'dh', '\u0A23': 'n',
  '\u0A24': 't', '\u0A25': 'th', '\u0A26': 'd', '\u0A27': 'dh', '\u0A28': 'n',
  '\u0A2A': 'p', '\u0A2B': 'ph', '\u0A2C': 'b', '\u0A2D': 'bh', '\u0A2E': 'm',
  '\u0A2F': 'y', '\u0A30': 'r', '\u0A32': 'l', '\u0A33': 'l', '\u0A35': 'v',
  '\u0A36': 'sh', '\u0A38': 's', '\u0A39': 'h',
  '\u0A59': 'kh', '\u0A5A': 'gh', '\u0A5B': 'z', '\u0A5C': 'r', '\u0A5E': 'f',
};

const GURU_NUKTA_MAP: Record<string, string> = {
  '\u0A16': 'kh', '\u0A17': 'gh', '\u0A1C': 'z', '\u0A2B': 'f', '\u0A32': 'l', '\u0A38': 'sh',
};

const GURU_MATRAS: Record<string, string> = {
  '\u0A3E': 'a', '\u0A3F': 'i', '\u0A40': 'i', '\u0A41': 'u', '\u0A42': 'u',
  '\u0A47': 'e', '\u0A48': 'ai', '\u0A4B': 'o', '\u0A4C': 'au',
};

function isGuruLetter(ch: string | undefined): boolean {
  if (!ch) return false;
  const c = ch.charCodeAt(0);
  return c >= 0x0a05 && c <= 0x0a74;
}

function romanizeGurmukhiWord(word: string): string {
  let out = '';
  let i = 0;
  let syllableCount = 0;
  let geminateNext = false;

  while (i < word.length) {
    const ch = word[i];
    if (ch === '\u0A71') {
      geminateNext = true;
      i++;
      continue;
    }
    if (ch === '\u0A70' || ch === '\u0A02' || ch === '\u0A01') {
      out += 'n';
      i++;
      continue;
    }
    if (ch >= '\u0A66' && ch <= '\u0A6F') {
      out += String(ch.charCodeAt(0) - 0x0a66);
      i++;
      continue;
    }
    if (ch === '\u0A72' || ch === '\u0A73') {
      const m = GURU_MATRAS[word[i + 1]];
      if (m) {
        out += m;
        i += 2;
      } else {
        i++;
      }
      syllableCount++;
      continue;
    }
    if (GURU_VOWELS[ch]) {
      out += GURU_VOWELS[ch];
      syllableCount++;
      i++;
      continue;
    }
    if (GURU_CONSONANTS[ch]) {
      let base = GURU_CONSONANTS[ch];
      i++;
      if (word[i] === '\u0A3C') {
        base = GURU_NUKTA_MAP[ch] ?? base;
        i++;
      }
      if (geminateNext) {
        base = base[0] + base;
        geminateNext = false;
      }
      if (word[i] === '\u0A4D') {
        out += base;
        i++;
        continue;
      }
      const matra = GURU_MATRAS[word[i]];
      if (matra) {
        out += base + matra;
        syllableCount++;
        i++;
        continue;
      }
      const atWordEnd = !isGuruLetter(word[i]);
      out += atWordEnd && syllableCount > 0 ? base : `${base}a`;
      syllableCount++;
      continue;
    }
    geminateNext = false;
    out += ch;
    i++;
  }
  return out;
}

// ---------------------------------------------------------------------------
// Korean Hangul (algorithmic Revised Romanization)
// ---------------------------------------------------------------------------
const HANGUL_LEAD = [
  'g', 'kk', 'n', 'd', 'tt', 'r', 'm', 'b', 'pp', 's', 'ss', '', 'j', 'jj', 'ch', 'k', 't', 'p', 'h',
];
const HANGUL_VOWEL = [
  'a', 'ae', 'ya', 'yae', 'eo', 'e', 'yeo', 'ye', 'o', 'wa', 'wae', 'oe', 'yo', 'u', 'wo', 'we', 'wi', 'yu', 'eu', 'ui', 'i',
];
const HANGUL_TAIL = [
  '', 'k', 'k', 'k', 'n', 'n', 'n', 't', 'l', 'l', 'l', 'l', 'l', 'l', 'l', 'l', 'm', 'p', 'p', 't', 't', 'ng', 't', 't', 'k', 't', 'p', 't',
];

function romanizeHangulChar(code: number): string {
  const idx = code - 0xac00;
  const lead = Math.floor(idx / 588);
  const vowel = Math.floor((idx % 588) / 28);
  const tail = idx % 28;
  return `${HANGUL_LEAD[lead]}${HANGUL_VOWEL[vowel]}${HANGUL_TAIL[tail]}`;
}

// ---------------------------------------------------------------------------
// Japanese Kana (Hiragana & Katakana -> Hepburn)
// ---------------------------------------------------------------------------
const KANA_DIGRAPHS: Record<string, string> = {
  きゃ: 'kya', きゅ: 'kyu', きょ: 'kyo', しゃ: 'sha', しゅ: 'shu', しょ: 'sho',
  ちゃ: 'cha', ちゅ: 'chu', ちょ: 'cho', にゃ: 'nya', にゅ: 'nyu', にょ: 'nyo',
  ひゃ: 'hya', ひゅ: 'hyu', ひょ: 'hyo', みゃ: 'mya', みゅ: 'myu', みょ: 'myo',
  りゃ: 'rya', りゅ: 'ryu', りょ: 'ryo', ぎゃ: 'gya', ぎゅ: 'gyu', ぎょ: 'gyo',
  じゃ: 'ja', じゅ: 'ju', じょ: 'jo', びゃ: 'bya', びゅ: 'byu', びょ: 'byo',
  ぴゃ: 'pya', ぴゅ: 'pyu', ぴょ: 'pyo',
};

const HIRAGANA_MAP: Record<string, string> = {
  あ: 'a', い: 'i', う: 'u', え: 'e', お: 'o',
  か: 'ka', き: 'ki', く: 'ku', け: 'ke', こ: 'ko',
  さ: 'sa', し: 'shi', す: 'su', せ: 'se', そ: 'so',
  た: 'ta', ち: 'chi', つ: 'tsu', て: 'te', と: 'to',
  な: 'na', に: 'ni', ぬ: 'nu', ね: 'ne', の: 'no',
  は: 'ha', ひ: 'hi', ふ: 'fu', へ: 'he', ほ: 'ho',
  ま: 'ma', み: 'mi', む: 'mu', め: 'me', も: 'mo',
  や: 'ya', ゆ: 'yu', よ: 'yo',
  ら: 'ra', り: 'ri', る: 'ru', れ: 're', ろ: 'ro',
  わ: 'wa', ゐ: 'wi', ゑ: 'we', を: 'wo', ん: 'n',
  が: 'ga', ぎ: 'gi', ぐ: 'gu', げ: 'ge', ご: 'go',
  ざ: 'za', じ: 'ji', ず: 'zu', ぜ: 'ze', ぞ: 'zo',
  だ: 'da', ぢ: 'ji', づ: 'zu', で: 'de', ど: 'do',
  ば: 'ba', び: 'bi', ぶ: 'bu', べ: 'be', ぼ: 'bo',
  ぱ: 'pa', ぴ: 'pi', ぷ: 'pu', ぺ: 'pe', ぽ: 'po',
  ぁ: 'a', ぃ: 'i', ぅ: 'u', ぇ: 'e', ぉ: 'o',
  ゃ: 'ya', ゅ: 'yu', ょ: 'yo',
};

function toHiragana(ch: string): string {
  const c = ch.charCodeAt(0);
  if (c >= 0x30a1 && c <= 0x30f6) return String.fromCharCode(c - 0x60);
  return ch;
}

// ---------------------------------------------------------------------------
// Urdu word-aware heuristics (avoids unreadable consonant-only outputs like "myn byth")
// ---------------------------------------------------------------------------
const URDU_DIACRITICS_RE = /[\u064B-\u065F\u0670\u06D6-\u06ED]/g;

const URDU_COMMON_WORDS: Record<string, string> = {
  میں: 'main', تو: 'tu', تم: 'tum', ہم: 'hum', وہ: 'woh', یہ: 'yeh',
  ہے: 'hai', ہیں: 'hain', ہوں: 'hoon', ہو: 'ho', تھا: 'tha', تھی: 'thi', تھے: 'the',
  اور: 'aur', پر: 'par', سے: 'se', کو: 'ko', کا: 'ka', کی: 'ki', کے: 'ke',
  نہ: 'na', نہیں: 'nahin', بھی: 'bhi', ہی: 'hi', جو: 'jo', جب: 'jab',
  اب: 'ab', سب: 'sab', دل: 'dil', عشق: 'ishq', پیار: 'pyaar', یاد: 'yaad',
  آ: 'aa', جا: 'jaa', جائے: 'jaaye', بیٹھ: 'baith', بیٹھے: 'baithe',
  لکھوں: 'likhoon', کہوں: 'kahoon', سو: 'so', سر: 'sar', رکھے: 'rakhe',
  نیند: 'neend', لفظ: 'lafz', نظم: 'nazm', شانے: 'shaane', تھامے: 'thaame',
  میرا: 'mera', میری: 'meri', میرے: 'mere',
  تیرا: 'tera', تیری: 'teri', تیرے: 'tere', تجھے: 'tujhe', مجھے: 'mujhe',
  کیا: 'kya', کیوں: 'kyun', کیسے: 'kaise', رات: 'raat', بات: 'baat',
  ساتھ: 'saath', آنکھوں: 'aankhon', خدا: 'khuda', دنیا: 'duniya',
  اے: 'ae', ان: 'un', اس: 'is', اک: 'ek', ایک: 'ek', ایسی: 'aisi',
};

const URDU_CHAR_MAP: Record<string, string> = {
  ا: 'a', آ: 'aa', ب: 'b', پ: 'p', ت: 't', ٹ: 't', ث: 's', ج: 'j', چ: 'ch',
  ح: 'h', خ: 'kh', د: 'd', ڈ: 'd', ذ: 'z', ر: 'r', ڑ: 'r', ز: 'z', ژ: 'zh',
  س: 's', ش: 'sh', ص: 's', ض: 'z', ط: 't', ظ: 'z', ع: 'a', غ: 'gh', ف: 'f',
  ق: 'q', ک: 'k', ك: 'k', گ: 'g', ل: 'l', م: 'm', ن: 'n', ں: 'n', و: 'o',
  ہ: 'h', ه: 'h', ھ: 'h', ء: '', ی: 'i', ي: 'i', ے: 'e', ة: 'h',
};

const URDU_VOWEL_CHARS = new Set(['ا', 'آ', 'و', 'ی', 'ي', 'ے', 'ع']);

function romanizeUrduWord(rawWord: string): string {
  const word = rawWord.replace(URDU_DIACRITICS_RE, '');
  if (!word) return '';
  const exact = URDU_COMMON_WORDS[word];
  if (exact) return exact;
  let out = '';
  for (let i = 0; i < word.length; i++) {
    const pair = word.slice(i, i + 2);
    if (pair === 'یں') {
      out += 'ein';
      i++;
      continue;
    }
    if (pair === 'وں') {
      out += 'oon';
      i++;
      continue;
    }
    if (pair === 'ئے') {
      out += 'ye';
      i++;
      continue;
    }
    const ch = word[i];
    if (ch === 'و' && i === 0) {
      out += 'w';
    } else if (ch === 'ی' && i === 0) {
      out += 'y';
    } else {
      out += URDU_CHAR_MAP[ch] ?? ch;
      // Insert short vowel 'a' between consecutive unvoweled Urdu consonants (skip aspirate 'ھ')
      const next = word[i + 1];
      if (
        next &&
        next !== 'ھ' &&
        ch !== 'ھ' &&
        !URDU_VOWEL_CHARS.has(ch) &&
        !URDU_VOWEL_CHARS.has(next) &&
        i + 1 < word.length &&
        !/[aeiou]$/.test(out)
      ) {
        out += 'a';
      }
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Cyrillic & Greek
// ---------------------------------------------------------------------------
const SINGLE_CHAR_MAP: Record<string, string> = {
  А: 'A', Б: 'B', В: 'V', Г: 'G', Д: 'D', Е: 'E', Ё: 'Yo', Ж: 'Zh', З: 'Z',
  И: 'I', Й: 'Y', К: 'K', Л: 'L', М: 'M', Н: 'N', О: 'O', П: 'P', Р: 'R',
  С: 'S', Т: 'T', У: 'U', Ф: 'F', Х: 'Kh', Ц: 'Ts', Ч: 'Ch', Ш: 'Sh', Щ: 'Shch',
  Ъ: '', Ы: 'Y', Ь: '', Э: 'E', Ю: 'Yu', Я: 'Ya',
  а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'yo', ж: 'zh', з: 'z',
  и: 'i', й: 'y', к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r',
  с: 's', т: 't', у: 'u', ф: 'f', х: 'kh', ц: 'ts', ч: 'ch', ш: 'sh', щ: 'shch',
  ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu', я: 'ya',
  Α: 'A', Β: 'V', Γ: 'G', Δ: 'D', Ε: 'E', Ζ: 'Z', Η: 'I', Θ: 'Th', Ι: 'I',
  Κ: 'K', Λ: 'L', Μ: 'M', Ν: 'N', Ξ: 'X', Ο: 'O', Π: 'P', Ρ: 'R', Σ: 'S',
  Τ: 'T', Υ: 'Y', Φ: 'F', Χ: 'Ch', Ψ: 'Ps', Ω: 'O',
  α: 'a', β: 'v', γ: 'g', δ: 'd', ε: 'e', ζ: 'z', η: 'i', θ: 'th', ι: 'i',
  κ: 'k', λ: 'l', μ: 'm', ν: 'n', ξ: 'x', ο: 'o', π: 'p', ρ: 'r', σ: 's', ς: 's',
  τ: 't', υ: 'y', φ: 'f', χ: 'ch', ψ: 'ps', ω: 'o',
};

const DEVA_RUN_RE = /[\u0900-\u097F]+/g;
const GURU_RUN_RE = /[\u0A00-\u0A7F]+/g;
const MALAYALAM_RUN_RE = /[\u0D00-\u0D7F]+/g;
const TAMIL_RUN_RE = /[\u0B80-\u0BFF]+/g;
const TELUGU_RUN_RE = /[\u0C00-\u0C7F]+/g;
const KANNADA_RUN_RE = /[\u0C80-\u0CFF]+/g;
const BENGALI_RUN_RE = /[\u0980-\u09FF]+/g;
const GUJARATI_RUN_RE = /[\u0A80-\u0AFF]+/g;
const URDU_RUN_RE = /[\u0600-\u06FF]+/g;

export function romanizeText(input: string): string {
  if (!input || !NON_LATIN_RE.test(input)) return input;

  // Step 1: Indic & Urdu word runs
  const text = input
    .replace(DEVA_RUN_RE, (m) => romanizeIndicWord(m, DEVA_SPEC))
    .replace(GURU_RUN_RE, (m) => romanizeGurmukhiWord(m))
    .replace(MALAYALAM_RUN_RE, (m) =>
      romanizeIndicWord(m, MALAYALAM_SPEC).replace(/njnj/g, 'nj').replace(/ngng/g, 'ng')
    )
    .replace(TAMIL_RUN_RE, (m) => romanizeIndicWord(m, TAMIL_SPEC))
    .replace(TELUGU_RUN_RE, (m) => romanizeIndicWord(m, TELUGU_SPEC))
    .replace(KANNADA_RUN_RE, (m) => romanizeIndicWord(m, KANNADA_SPEC))
    .replace(BENGALI_RUN_RE, (m) => romanizeIndicWord(m, BENGALI_SPEC))
    .replace(GUJARATI_RUN_RE, (m) => romanizeIndicWord(m, GUJARATI_SPEC))
    .replace(URDU_RUN_RE, (m) => romanizeUrduWord(m));

  if (!NON_LATIN_RE.test(text)) return text;

  // Step 2: Hangul, Kana, Cyrillic, Greek
  let out = '';
  let geminateKana = false;

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    const code = ch.charCodeAt(0);

    if (code >= 0xac00 && code <= 0xd7a3) {
      out += romanizeHangulChar(code);
      continue;
    }

    if ((code >= 0x3040 && code <= 0x309f) || (code >= 0x30a0 && code <= 0x30ff)) {
      if (ch === 'ー') {
        const last = out[out.length - 1];
        if (last && 'aeiou'.includes(last)) out += last;
        continue;
      }
      const hira = toHiragana(ch);
      if (hira === 'っ') {
        geminateKana = true;
        continue;
      }
      const nextHira = i + 1 < text.length ? toHiragana(text[i + 1]) : '';
      const digraph = nextHira ? KANA_DIGRAPHS[hira + nextHira] : undefined;
      let rom = digraph ?? HIRAGANA_MAP[hira] ?? ch;
      if (digraph) i++;
      if (geminateKana && rom.length > 0 && /[a-z]/.test(rom[0])) {
        rom = rom[0] + rom;
      }
      geminateKana = false;
      out += rom;
      continue;
    }

    geminateKana = false;

    const mapped = SINGLE_CHAR_MAP[ch];
    if (mapped !== undefined) {
      out += mapped;
      continue;
    }

    out += ch;
  }

  return out;
}

.pragma library

// DialogUtils.js — pure helpers shared by the dialog editors (no QML context access).
//
//   normaliseFiles(list) → [{path, uri, name, stem, ext, category, mime, size, width, height, pages, duration,
//                            icon, animated, thumb}]   accepts FileInfo JSON objects, paths or file:// URLs
//   jobTarget(file) → path (or the URL for web files) to send to Jobs.run
//   clamp(v, lo, hi), num(v) → finite number | null, copy(v) (JSON deep copy), same(a, b) (JSON equality)
//   baseName(path), extOf(name)
//   formatTime(seconds, withMs = true) → "hh:mm:ss.mmm" ; parseTime(text) → seconds | NaN
//   validatePages(text, count) → "" | "empty" | "syntax" | "range" | "none"   (backend syntax: 1-3,5,8-z,7-,r1,odd,x4)
//   stripEllipsis(label) → the label without a trailing "…"
//   isUrl(text) → bool (http/https/ftp URL or www.…)   urlFor(text) → openable URL
//   parseNumber(text, integer) → number | NaN (accepts "1,5")
//   ratioOf(id) → numeric w/h ratio for "16:9" style ids, null for free/original

function isObject(v) {
    return v !== null && typeof v === "object" && !Array.isArray(v);
}

function num(v) {
    if (v === null || v === undefined || v === "" || typeof v === "boolean")
        return null;
    const n = Number(v);
    return isFinite(n) ? n : null;
}

function clamp(v, lo, hi) {
    const n = Number(v);
    if (!isFinite(n))
        return lo;
    return Math.max(lo, Math.min(hi, n));
}

function copy(v) {
    if (v === undefined)
        return undefined;
    try {
        return JSON.parse(JSON.stringify(v));
    } catch (e) {
        return v;
    }
}

function same(a, b) {
    if (a === b)
        return true;
    try {
        return JSON.stringify(a) === JSON.stringify(b);
    } catch (e) {
        return false;
    }
}

function decodePath(s) {
    let p = String(s ?? "");
    if (!p.startsWith("file://"))
        return p;
    p = p.slice(7);
    if (!p.startsWith("/")) {
        const slash = p.indexOf("/");
        p = slash >= 0 ? p.slice(slash) : "";
    }
    try {
        return decodeURIComponent(p);
    } catch (e) {
        // Not UTF-8 (a Latin-1 name): the URL itself still names the file, the daemon decodes it to the same bytes.
        return String(s);
    }
}

function baseName(path) {
    const p = String(path ?? "").replace(/\/+$/, "");
    const i = p.lastIndexOf("/");
    return i >= 0 ? p.slice(i + 1) : p;
}

function extOf(name) {
    const n = String(name ?? "");
    const i = n.lastIndexOf(".");
    if (i <= 0 || i === n.length - 1)
        return "";
    return n.slice(i + 1).toLowerCase();
}

function stemOf(name) {
    const n = String(name ?? "");
    const i = n.lastIndexOf(".");
    return i <= 0 ? n : n.slice(0, i);
}

function normaliseFile(f) {
    if (f === null || f === undefined)
        return null;
    if (typeof f === "string" || typeof f === "number") {
        const raw = String(f);
        if (raw === "")
            return null;
        const isWeb = /^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(raw) && !raw.startsWith("file://");
        const path = isWeb ? "" : decodePath(raw);
        const name = isWeb ? raw : baseName(path);
        return {
            path: path,
            uri: isWeb ? raw : (raw.startsWith("file://") ? raw : ""),
            name: name,
            stem: stemOf(name),
            ext: isWeb ? "" : extOf(name),
            category: isWeb ? "url" : "",
            mime: "",
            size: null,
            width: null,
            height: null,
            pages: null,
            duration: null,
            icon: "",
            animated: false,
            thumb: ""
        };
    }
    if (!isObject(f))
        return null;
    const rawPath = f.path !== undefined && f.path !== null ? String(f.path) : "";
    const uri = f.uri !== undefined && f.uri !== null ? String(f.uri) : "";
    const path = rawPath !== "" ? decodePath(rawPath) : (uri.startsWith("file://") ? decodePath(uri) : "");
    if (path === "" && uri === "")
        return null;
    const name = (typeof f.name === "string" && f.name !== "") ? f.name : (path !== "" ? baseName(path) : uri);
    return {
        path: path,
        uri: uri,
        name: name,
        stem: (typeof f.stem === "string" && f.stem !== "") ? f.stem : stemOf(name),
        ext: (typeof f.ext === "string") ? f.ext.toLowerCase() : extOf(name),
        category: typeof f.category === "string" ? f.category : "",
        mime: typeof f.mime === "string" ? f.mime : "",
        size: num(f.size),
        width: num(f.width),
        height: num(f.height),
        pages: num(f.pages),
        duration: num(f.duration),
        icon: typeof f.icon === "string" ? f.icon : "",
        animated: f.animated === true,
        thumb: typeof f.thumb === "string" ? f.thumb : ""
    };
}

function normaliseFiles(list) {
    const out = [];
    if (list === null || list === undefined)
        return out;
    if (typeof list === "string") {
        const one = normaliseFile(list);
        return one ? [one] : out;
    }
    const n = list.length ?? 0;
    for (let i = 0; i < n; i++) {
        const f = normaliseFile(list[i]);
        if (f !== null)
            out.push(f);
    }
    return out;
}

function jobTarget(file) {
    if (!file)
        return "";
    return file.path !== "" ? file.path : file.uri;
}

function pad(n, width) {
    let s = String(Math.floor(Math.abs(n)));
    while (s.length < width)
        s = "0" + s;
    return s;
}

function formatTime(seconds, withMs) {
    const v = Number(seconds);
    if (!isFinite(v))
        return "";
    const ms = Math.round(Math.max(0, v) * 1000);
    const h = Math.floor(ms / 3600000);
    const m = Math.floor((ms % 3600000) / 60000);
    const s = Math.floor((ms % 60000) / 1000);
    const rest = ms % 1000;
    const base = pad(h, 2) + ":" + pad(m, 2) + ":" + pad(s, 2);
    return withMs === false ? base : base + "." + pad(rest, 3);
}

function parseTime(text) {
    const t = String(text ?? "").trim().replace(/,/g, ".").replace(/\s*s$/i, "");
    if (t === "")
        return NaN;
    let m = /^(\d+(?:\.\d*)?|\.\d+)$/.exec(t);
    if (m)
        return Number(m[1]);
    m = /^(\d+):(\d{1,2}(?:\.\d*)?)$/.exec(t);
    if (m) {
        const sec = Number(m[2]);
        return sec < 60 ? Number(m[1]) * 60 + sec : NaN;
    }
    m = /^(\d+):(\d{1,2}):(\d{1,2}(?:\.\d*)?)$/.exec(t);
    if (m) {
        const min = Number(m[2]);
        const sec = Number(m[3]);
        return (min < 60 && sec < 60) ? Number(m[1]) * 3600 + min * 60 + sec : NaN;
    }
    return NaN;
}

function parseNumber(text, integer) {
    const t = String(text ?? "").trim().replace(",", ".");
    if (t === "" || !/^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(t))
        return NaN;
    const n = Number(t);
    if (!isFinite(n))
        return NaN;
    if (integer && Math.floor(n) !== n)
        return NaN;
    return n;
}

// Page selections exactly as the backend reads them (pdf.parse_pages): tokens separated by commas, semicolons or
// spaces — "5", "1-3", "5-1" (reversed), "8-z" / "8-end" / "8-" (to the last page), "-3" (from the first), "r1" (the
// last page), "odd" / "dispari", "even" / "pari", "all" / "tutte"; "x4" or "!3-4" excludes pages (a list of only
// exclusions keeps all the other pages). There is no qpdf ":odd" suffix.
const PAGE_END_WORDS = ["z", "end", "fine", "last", "ultima", "ultimo", "n"];
const PAGE_ODD_WORDS = ["odd", "dispari"];
const PAGE_EVEN_WORDS = ["even", "pari"];
const PAGE_ALL_WORDS = ["all", "tutte", "tutto", "*"];

// One page reference → 1-based page (0 when the page count is unknown) or an error code.
function pageNumber(tok, total) {
    const low = String(tok).toLowerCase();
    if (PAGE_END_WORDS.indexOf(low) !== -1)
        return total;
    const m = /^r(\d+)$/.exec(low);
    if (m) {
        const back = Number(m[1]);
        if (back < 1 || (total > 0 && back > total))
            return "range";
        return total > 0 ? total - back + 1 : 0;
    }
    if (/^\d+$/.test(low)) {
        const n = Number(low);
        if (n < 1 || (total > 0 && n > total))
            return "range";
        return n;
    }
    return "syntax";
}

function pageSeq(from, to, step) {
    const out = [];
    if (step > 0) {
        for (let p = from; p <= to; p += step)
            out.push(p);
    } else {
        for (let p = from; p >= to; p += step)
            out.push(p);
    }
    return out;
}

// One token → its pages (empty when the page count is unknown) or an error code.
function pageToken(tok, total) {
    const low = tok.toLowerCase();
    if (PAGE_ALL_WORDS.indexOf(low) !== -1)
        return pageSeq(1, total, 1);
    if (PAGE_ODD_WORDS.indexOf(low) !== -1)
        return pageSeq(1, total, 2);
    if (PAGE_EVEN_WORDS.indexOf(low) !== -1)
        return pageSeq(2, total, 2);
    const dash = tok.indexOf("-");
    if (dash === -1) {
        const n = pageNumber(tok, total);
        if (typeof n === "string")
            return n;
        return total > 0 ? [n] : [];
    }
    const a = tok.slice(0, dash);
    const b = tok.slice(dash + 1);
    const start = a !== "" ? pageNumber(a, total) : 1;
    if (typeof start === "string")
        return start;
    let end = total;
    if (b !== "") {
        end = pageNumber(b, total);
        if (typeof end === "string") {
            if (end === "range" && /^\d+$/.test(b) && total > 0 && Number(b) > total)
                end = total; // "1-100" on a 5-page document means "1-5"
            else
                return end;
        }
    }
    if (total <= 0)
        return [];
    return pageSeq(start, end, end >= start ? 1 : -1);
}

// → "" (valid) | "empty" | "syntax" | "range" (a page outside the document) | "none" (nothing left selected).
// count: the page count when known (numbers are then checked against it), otherwise only the syntax is checked.
function validatePages(text, count) {
    const t = String(text ?? "").trim().replace(/[–—]/g, "-").replace(/\.\./g, "-").replace(/\s*-\s*/g, "-");
    if (t === "")
        return "empty";
    const n = Number(count);
    const total = isFinite(n) && n > 0 ? Math.floor(n) : 0;
    const tokens = t.split(/[,;\s]+/).filter(s => s !== "");
    if (tokens.length === 0)
        return "syntax";
    const picked = [];
    const excluded = {};
    let onlyExclusions = true;
    for (const tok of tokens) {
        const exclusion = tok.length > 1 && /^[xX!]/.test(tok) && /^[0-9rRzZ-]/.test(tok[1]);
        const pages = pageToken(exclusion ? tok.slice(1) : tok, total);
        if (typeof pages === "string")
            return pages;
        if (exclusion) {
            for (const p of pages)
                excluded[p] = true;
        } else {
            onlyExclusions = false;
            for (const p of pages)
                picked.push(p);
        }
    }
    if (total === 0)
        return "";
    const pool = onlyExclusions ? pageSeq(1, total, 1) : picked;
    return pool.some(p => !excluded[p]) ? "" : "none";
}

// "Rinomina…" → "Rinomina": a menu label used as a dialog title or a button that starts the job.
function stripEllipsis(text) {
    return String(text ?? "").replace(/\s*(…|\.\.\.)\s*$/, "");
}

function isUrl(text) {
    const t = String(text ?? "").trim();
    if (t === "" || /\s/.test(t) || t.length > 4096)
        return false;
    return /^(https?|ftp):\/\/[^\s/$.?#].[^\s]*$/i.test(t) || /^www\.[^\s.]+\.[^\s]{2,}$/i.test(t);
}

function urlFor(text) {
    const t = String(text ?? "").trim();
    return /^www\./i.test(t) ? "https://" + t : t;
}

function ratioOf(id) {
    const m = /^(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)$/.exec(String(id ?? ""));
    if (!m)
        return null;
    const a = Number(m[1]);
    const b = Number(m[2]);
    return (a > 0 && b > 0) ? a / b : null;
}

// ==UserScript==
// @name         Gemini Webpage Translator
// @namespace    http://tampermonkey.net/
// @version      7.1.0
// @description  Translate webpages using Gemini or OpenRouter API.
// @author       NordLandeW
// @match        *://*/*
// @exclude      *://challenges.cloudflare.com/*
// @exclude      *://*/cdn-cgi/challenge-platform/*
// @exclude      *://*/cdn-cgi/l/chk_*
// @exclude      *://*/cdn-cgi/access/*
// @exclude      *://*.alipay.com/*
// @exclude      *://*.taobao.com/*
// @exclude      *://*.tmall.com/*
// @exclude      *://*.jd.com/*
// @exclude      *://*.pinduoduo.com/*
// @exclude      *://*.suning.com/*
// @exclude      *://*.meituan.com/*
// @exclude      *://pay.weixin.qq.com/*
// @exclude      *://*.95516.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_deleteValue
// @grant        GM_registerMenuCommand
// @connect      generativelanguage.googleapis.com
// @connect      openrouter.ai
// @run-at       document-idle
// ==/UserScript==

(function() {
    'use strict';

    // ================= Configuration =================
    const CONFIG = {
        platforms: {
            gemini: {
                name: 'Google Gemini',
                endpoint: 'https://generativelanguage.googleapis.com/v1beta/models',
                models: [
                    'gemini-2.5-pro',
                    'gemini-3.1-pro-preview',
                    'gemini-3-flash-preview',
                    'gemini-3.1-flash-lite-preview',
                ]
            },
            openrouter: {
                name: 'OpenRouter',
                endpoint: 'https://openrouter.ai/api/v1/chat/completions',
                models: [
                    'google/gemini-2.5-pro',
                    'google/gemini-3-flash-preview',
                    'google/gemini-3.1-flash-lite-preview',
                ]
            }
        },
        cachePrefix: 'gm_cache_v7_',
        cacheMetaKey: 'gm_cache_meta',
        defaults: {
            platform: 'gemini',
            maxCacheSize: 10 * 1024 * 1024, // 10MB
            cacheTTL: 7 * 24 * 60 * 60 * 1000, // 7 days
            urlSimilarityThreshold: 0.95,
            batchNodeCount: 500,
            concurrency: 8,
            maxBatchRetries: 2
        }
    };

    const state = {
        isTranslating: false,
        isTranslated: false,
        isUIInitialized: false,
        originalTexts: new Map(),
        textNodeMap: new Map(),
        currentToastId: null,
        translationProgress: null,
        lastProgressUpdateAt: 0
    };

    // ================= 样式表 =================
    const STYLES = `
        :root { --gm-primary: #8ab4f8; --gm-bg: #202124; --gm-surface: #303134; --gm-text: #e8eaed; --gm-border: #5f6368; }
        #gm-translator-container { display: block; font-family: 'Segoe UI', system-ui, sans-serif; z-index: 2147483647; position: fixed; top: 0; left: 0; color-scheme: dark; }
        
        /* Modern Toast */
        .gm-toast {
            position: fixed; top: 24px; right: 24px;
            background: rgba(32, 33, 36, 0.9);
            border: 1px solid rgba(255,255,255,0.08); color: var(--gm-text);
            padding: 12px 24px; border-radius: 12px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.4); font-size: 14px; font-weight: 500;
            display: flex; align-items: center; gap: 12px;
            transform: translateY(-20px) scale(0.95); opacity: 0;
            transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
            backdrop-filter: blur(12px);
            max-width: 420px;
        }
        .gm-toast.visible { transform: translateY(0) scale(1); opacity: 1; }
        
        .gm-spinner {
            width: 18px; height: 18px; border: 2.5px solid rgba(138, 180, 248, 0.2);
            border-top-color: var(--gm-primary); border-radius: 50%;
            animation: gm-spin 1s cubic-bezier(0.4, 0, 0.2, 1) infinite;
        }
        @keyframes gm-spin { to { transform: rotate(360deg); } }

        /* Modern Settings Overlay */
        #gm-settings-overlay {
            position: fixed; inset: 0; background: rgba(0,0,0,0.7);
            display: flex; justify-content: center; align-items: center;
            opacity: 0; pointer-events: none; transition: opacity 0.3s ease;
            backdrop-filter: blur(4px);
        }
        #gm-settings-overlay.open { opacity: 1; pointer-events: auto; }
        
        #gm-settings-panel {
            background: #2b2c30; border: 1px solid rgba(255,255,255,0.1);
            width: 420px; border-radius: 16px; padding: 32px;
            box-shadow: 0 24px 48px rgba(0,0,0,0.5);
            transform: scale(0.92) translateY(10px); transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
            display: flex; flex-direction: column; gap: 16px;
        }
        #gm-settings-overlay.open #gm-settings-panel { transform: scale(1) translateY(0); }

        /* Form Elements */
        .gm-label { color: #9aa0a6; font-size: 12px; font-weight: 600; margin-bottom: 6px; display: block; letter-spacing: 0.5px; text-transform: uppercase;}
        
        .gm-input, .gm-select {
            width: 100%; background: rgba(0,0,0,0.2); border: 1px solid var(--gm-border);
            color: #fff; padding: 10px 14px; border-radius: 8px; font-size: 14px;
            box-sizing: border-box; outline: none; transition: all 0.2s;
        }
        .gm-select option { background: #2b2c30; color: #fff; }
        .gm-input:focus, .gm-select:focus { border-color: var(--gm-primary); background: rgba(138, 180, 248, 0.05); box-shadow: 0 0 0 2px rgba(138, 180, 248, 0.2); }
        .gm-input:hover, .gm-select:hover { border-color: #888; }
        
        .gm-input-secret { -webkit-text-security: disc; text-security: disc; letter-spacing: 2px; }
        
        .gm-checkbox-group { display: flex; flex-direction: column; gap: 10px; margin: 6px 0; background: rgba(0,0,0,0.15); padding: 12px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.05); }
        .gm-checkbox-label { color: #e8eaed; font-size: 13px; display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none; }
        .gm-checkbox-label input { accent-color: var(--gm-primary); width: 16px; height: 16px; cursor: pointer; }

        /* Buttons */
        .gm-btn-group { display: flex; justify-content: flex-end; gap: 12px; margin-top: 10px; }
        .gm-btn { padding: 10px 20px; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; border: none; transition: all 0.2s; display: flex; align-items: center; justify-content: center; }
        .gm-btn:active { transform: scale(0.96); }
        
        .gm-btn-primary { background: var(--gm-primary); color: #202124; box-shadow: 0 2px 8px rgba(138, 180, 248, 0.3); }
        .gm-btn-primary:hover { background: #a1c3f9; box-shadow: 0 4px 12px rgba(138, 180, 248, 0.4); }
        
        .gm-btn-secondary { background: rgba(255,255,255,0.08); color: #e8eaed; }
        .gm-btn-secondary:hover { background: rgba(255,255,255,0.12); }
        
        .gm-btn-danger { background: rgba(239, 68, 68, 0.15); color: #fca5a5; font-size: 12px; padding: 8px 16px; }
        .gm-btn-danger:hover { background: rgba(239, 68, 68, 0.25); }

        #gm-cache-stats { font-family: monospace; font-size: 12px; color: #9aa0a6; text-align: center; margin-top: 4px;}
        .gm-hidden { display: none !important; }
    `;

    // ================= 缓存管理 =================
    
    // Normalize URLs for cache consistency
    function normalizeUrl(rawUrl) {
        try {
            const url = new URL(rawUrl, window.location.origin);
            let normalized = `${url.protocol}//${url.host}${url.pathname}`;
            if (normalized.length > 1 && normalized.endsWith('/')) {
                normalized = normalized.slice(0, -1);
            }
            return normalized;
        } catch {
            const stripped = rawUrl.split('#')[0].split('?')[0];
            if (stripped.length > 1 && stripped.endsWith('/')) {
                return stripped.slice(0, -1);
            }
            return stripped;
        }
    }

    function getUrlSimilarityThreshold() {
        const stored = GM_getValue('gm_url_similarity_threshold', CONFIG.defaults.urlSimilarityThreshold);
        const value = typeof stored === 'number' ? stored : parseFloat(stored);
        if (Number.isNaN(value)) return CONFIG.defaults.urlSimilarityThreshold;
        return Math.min(Math.max(value, 0), 1);
    }

    function getClampedIntegerSetting(key, defaultValue, min, max) {
        const stored = GM_getValue(key, defaultValue);
        const value = typeof stored === 'number' ? stored : parseInt(stored, 10);
        if (Number.isNaN(value)) return defaultValue;
        return Math.min(Math.max(value, min), max);
    }

    function readClampedIntegerFromInput(elementId, defaultValue, min, max) {
        const input = document.getElementById(elementId);
        const value = parseInt(input.value, 10);
        if (Number.isNaN(value)) return defaultValue;
        return Math.min(Math.max(value, min), max);
    }

    // Compute Levenshtein distance between two strings
    function levenshteinDistance(a, b) {
        const m = a.length;
        const n = b.length;
        if (m === 0) return n;
        if (n === 0) return m;

        const dp = new Array(n + 1);
        for (let j = 0; j <= n; j++) dp[j] = j;

        for (let i = 1; i <= m; i++) {
            let prev = dp[0];
            dp[0] = i;
            const ca = a.charCodeAt(i - 1);
            for (let j = 1; j <= n; j++) {
                const tmp = dp[j];
                const cost = ca === b.charCodeAt(j - 1) ? 0 : 1;
                dp[j] = Math.min(
                    dp[j] + 1,
                    dp[j - 1] + 1,
                    prev + cost
                );
                prev = tmp;
            }
        }

        return dp[n];
    }

    // Convert edit distance to similarity score [0,1]
    function stringSimilarity(a, b) {
        if (a === b) return 1;
        const maxLen = Math.max(a.length, b.length);
        if (maxLen === 0) return 1;
        const dist = levenshteinDistance(a, b);
        return (maxLen - dist) / maxLen;
    }

    // Find best cache entry using exact and fuzzy URL matching
    function findBestCacheKeyForUrl(rawUrl, meta) {
        const normalizedUrl = normalizeUrl(rawUrl);
        const threshold = getUrlSimilarityThreshold();
        const exactKey = generateCacheKey(normalizedUrl);

        let bestKey = null;
        let bestEntry = null;
        let bestScore = 0;

        if (meta[exactKey]) {
            bestKey = exactKey;
            bestEntry = meta[exactKey];
            bestScore = 1;
        }

        for (const [key, entry] of Object.entries(meta)) {
            if (key === exactKey) continue;
            const candidateUrl = entry.normalizedUrl || normalizeUrl(entry.url || '');
            const score = stringSimilarity(normalizedUrl, candidateUrl);
            if (score >= threshold && score > bestScore) {
                bestKey = key;
                bestEntry = entry;
                bestScore = score;
            }
        }

        return { key: bestKey, entry: bestEntry, normalizedUrl };
    }

    // Generate cache key using simple hash
    function generateCacheKey(url) {
        let hash = 0;
        for (let i = 0; i < url.length; i++) {
            const char = url.charCodeAt(i);
            hash = ((hash << 5) - hash) + char;
            hash = hash & hash;
        }
        return CONFIG.cachePrefix + Math.abs(hash).toString(36);
    }
    
    // Get cache metadata
    function getCacheMeta() {
        const meta = GM_getValue(CONFIG.cacheMetaKey, '{}');
        try {
            return JSON.parse(meta);
        } catch {
            return {};
        }
    }
    
    // Save cache metadata
    function saveCacheMeta(meta) {
        GM_setValue(CONFIG.cacheMetaKey, JSON.stringify(meta));
    }
    
    // Get cache with TTL validation
    function getCache(rawUrl) {
        const meta = getCacheMeta();
        const { key, entry } = findBestCacheKeyForUrl(rawUrl, meta);

        if (!key || !entry) return null;

        const ttl = GM_getValue('gm_cache_ttl', CONFIG.defaults.cacheTTL);
        const now = Date.now();

        // ttl <= 0 means "never expire"
        if (ttl > 0 && now - entry.timestamp > ttl) {
            deleteCache(key);
            return null;
        }

        const value = GM_getValue(key, null);

        // Clean up orphaned metadata
        if (value == null) {
            deleteCache(key);
            return null;
        }

        return value;
    }
    
    // Set cache with size limit enforcement
    function setCache(rawUrl, data) {
        const normalizedUrl = normalizeUrl(rawUrl);
        const key = generateCacheKey(normalizedUrl);
        const meta = getCacheMeta();
        const dataSize = new Blob([data]).size;
        const maxSize = GM_getValue('gm_max_cache_size', CONFIG.defaults.maxCacheSize);

        let totalSize = Object.values(meta).reduce(
            (sum, entry) => sum + (entry.size || 0),
            0
        );

        // Evict oldest entries if over size limit
        while (totalSize + dataSize > maxSize && Object.keys(meta).length > 0) {
            const oldestKey = Object.keys(meta).reduce((oldest, k) =>
                !oldest || meta[k].timestamp < meta[oldest].timestamp ? k : oldest
            , null);

            if (!oldestKey) break;

            totalSize -= meta[oldestKey].size || 0;
            GM_deleteValue(oldestKey);
            delete meta[oldestKey];
        }

        GM_setValue(key, data);
        meta[key] = {
            url: rawUrl,
            normalizedUrl,
            timestamp: Date.now(),
            size: dataSize
        };
        saveCacheMeta(meta);
    }
    
    // Delete specific cache entry
    function deleteCache(key) {
        const meta = getCacheMeta();
        GM_deleteValue(key);
        delete meta[key];
        saveCacheMeta(meta);
    }
    
    // Delete cache for current page
    function clearCurrentPageCache() {
        const meta = getCacheMeta();
        const { key } = findBestCacheKeyForUrl(window.location.href, meta);
        if (!key) return;
        deleteCache(key);
    }
    
    // Clear all cache entries
    function clearAllCache() {
        const meta = getCacheMeta();
        Object.keys(meta).forEach(key => GM_deleteValue(key));
        GM_deleteValue(CONFIG.cacheMetaKey);
    }
    
    // Get cache statistics
    function getCacheStats() {
        const meta = getCacheMeta();
        const entries = Object.values(meta);
        const totalSize = entries.reduce((sum, entry) => sum + (entry.size || 0), 0);
        const count = entries.length;
        return { totalSize, count };
    }
    
    // Format bytes to human-readable size
    function formatBytes(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    function buildTranslationPrompt() {
        return `You are a CSV translator.
Input format: id,"text content"
Task: Translate "text content" to Simplified Chinese.

Rules:
1. Keep each "id" exactly as-is.
2. Output exactly one CSV line for every input line, in the same order: id,"translated_text".
3. Escape any double quotes in the translated text by doubling them (e.g., " becomes "").
4. If the text is already Chinese (i.e., contains predominantly Chinese characters), keep it unchanged.
5. Do not translate proper nouns (names, brands, places, organizations, etc.). Keep them in the original language.
6. For specialist or technical terms without a widely accepted Chinese translation, keep them in English.
7. Preserve all HTML markup, placeholders, variables, and format tokens exactly as they appear.
8. Keep sentence structure close to the original. Do not merge, split, omit, or reorder records.
9. Never output Markdown fences, CSV headers, explanations, or blank lines.
10. Never include newline characters inside translated_text.

Provide only the translated CSV lines.
`;
    }

    function createTranslationBatches(entries, batchNodeCount) {
        const batches = [];
        for (let i = 0; i < entries.length; i += batchNodeCount) {
            const slice = entries.slice(i, i + batchNodeCount);
            batches.push({
                index: batches.length,
                entries: slice,
                csvInput: buildCsvFromEntries(slice),
                resultLines: [],
                pendingCsvBuffer: '',
                appliedIds: new Set(),
                attempts: 0
            });
        }
        return batches;
    }

    function createProgressState(totalNodes, totalBatches, workerCount) {
        return {
            totalNodes,
            translatedNodes: 0,
            totalBatches,
            finishedBatches: 0,
            failedBatches: 0,
            runningBatches: 0,
            workerCount,
            retriedBatches: 0
        };
    }

    function getProgressText() {
        const progress = state.translationProgress;
        if (!progress) return '正在翻译...';

        const percent = progress.totalNodes === 0
            ? 0
            : Math.floor((progress.translatedNodes / progress.totalNodes) * 100);
        const retryText = progress.retriedBatches > 0 ? ` | 重试 ${progress.retriedBatches}` : '';
        const failureText = progress.failedBatches > 0 ? ` | 失败 ${progress.failedBatches}` : '';

        return `翻译中 ${progress.translatedNodes}/${progress.totalNodes} (${percent}%) | 批次 ${progress.finishedBatches}/${progress.totalBatches} | 活动 ${progress.runningBatches}/${progress.workerCount}${retryText}${failureText}`;
    }

    function refreshProgressToast(force = false) {
        if (!state.currentToastId || !state.translationProgress) return;

        const now = Date.now();
        if (!force && now - state.lastProgressUpdateAt < 120) return;

        state.lastProgressUpdateAt = now;
        state.currentToastId = updateToast(state.currentToastId, getProgressText(), 'loading');
    }

    // ================= Initialization =================

    /**
     * Cloudflare verification pages are sensitive to third-party DOM changes and event listeners.
     * Disabling the script on those pages avoids breaking the challenge flow.
     */
    /**
     * Checks if the current page is a sensitive context where the script should be disabled.
     * This includes Cloudflare verification pages and major Chinese shopping/payment sites.
     */
    function isExcludedContext() {
        try {
            const host = window.location.hostname;

            // Cloudflare protection
            if (host === 'challenges.cloudflare.com') return true;

            const path = window.location.pathname || '';
            if (path.startsWith('/cdn-cgi/challenge-platform/')) return true;
            if (path.startsWith('/cdn-cgi/l/chk_')) return true;
            if (path.startsWith('/cdn-cgi/access/')) return true;

            // Cloudflare interstitial pages usually expose this config globally.
            if (typeof window._cf_chl_opt !== 'undefined') return true;

            // Fallback DOM markers.
            if (document.querySelector('form#challenge-form')) return true;
            if (document.getElementById('cf-wrapper')) return true;
            if (document.querySelector('script[src*="/cdn-cgi/challenge-platform/"]')) return true;

            // Domestic shopping and payment sites
            const sensitiveDomains = [
                'alipay.com',
                'taobao.com',
                'tmall.com',
                'jd.com',
                'pinduoduo.com',
                'suning.com',
                'meituan.com',
                'pay.weixin.qq.com',
                '95516.com'
            ];
            if (sensitiveDomains.some(domain => host === domain || host.endsWith('.' + domain))) return true;

            return false;
        } catch {
            return false;
        }
    }

    function ensureUI() {
        if (state.isUIInitialized) return;

        const style = document.createElement('style');
        style.textContent = STYLES;
        document.head.appendChild(style);

        createUI();
        state.isUIInitialized = true;
    }

    function init() {
        GM_registerMenuCommand("🚀 翻译网页 (Alt+T)", startTranslationProcess);
        GM_registerMenuCommand("⚙️ 设置", openSettings);

        document.addEventListener('keydown', (e) => {
            if (e.altKey && e.key.toLowerCase() === 't') { e.preventDefault(); startTranslationProcess(); }
            if (e.altKey && e.key.toLowerCase() === 's') { e.preventDefault(); openSettings(); }
        });
        
        console.log('[Gemini Translator] Ready.');
    }

    // ================= Text Node Extraction =================

    // Extract translatable text nodes from page
    function extractTextNodes() {
        const walker = document.createTreeWalker(
            document.body,
            NodeFilter.SHOW_TEXT,
            {
                acceptNode: function(node) {
                    const parent = node.parentElement;
                    if (!parent) return NodeFilter.FILTER_REJECT;
                    
                    // Tag blacklist
                    const tag = parent.tagName.toLowerCase();
                    if (['script', 'style', 'noscript', 'textarea', 'code', 'pre', 'svg', 'path', 'kbd', 'var'].includes(tag)) {
                        return NodeFilter.FILTER_REJECT;
                    }

                    // Skip no-translate elements
                    if (parent.closest('[translate="no"], .notranslate')) {
                        return NodeFilter.FILTER_REJECT;
                    }

                    // Visibility check
                    const style = window.getComputedStyle(parent);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                        return NodeFilter.FILTER_REJECT;
                    }

                    // Filter whitespace-only or numeric-only content
                    const text = node.nodeValue;
                    if (!/[^\s\d]/.test(text)) {
                        return NodeFilter.FILTER_REJECT;
                    }

                    return NodeFilter.FILTER_ACCEPT;
                }
            }
        );

        const nodes = [];
        let n;
        while (n = walker.nextNode()) nodes.push(n);
        return nodes;
    }

    // ================= CSV Processing =================

    // Generate translation entries and node mapping
    function generateTranslationEntries(nodes) {
        state.textNodeMap.clear();
        const tagCounters = {};

        return nodes.map((node, index) => {
            const tag = node.parentElement.tagName.toUpperCase();
            if (!tagCounters[tag]) tagCounters[tag] = 0;

            const id = `${tag}_${tagCounters[tag]++}_${index}`;
            state.textNodeMap.set(id, node);

            return {
                id,
                node,
                text: node.nodeValue.replace(/"/g, '""').replace(/[\r\n]+/g, ' ')
            };
        });
    }

    function buildCsvFromEntries(entries) {
        return entries.map(entry => `${entry.id},"${entry.text}"`).join('\n');
    }

    function parseCsvTranslationLine(rawLine) {
        const line = rawLine.trim();
        if (!line || /^```(?:csv)?\s*$/i.test(line) || /^id\s*,/i.test(line)) {
            return null;
        }

        const match = line.match(/^([^,]+),"(.*)"$/);
        if (!match) return null;

        return {
            line,
            id: match[1],
            text: match[2].replace(/""/g, '"')
        };
    }

    function applyPreparedTranslation(preparedLine) {
        const node = state.textNodeMap.get(preparedLine.id);
        if (!node) return 0;

        if (!state.originalTexts.has(node)) {
            state.originalTexts.set(node, node.nodeValue);
        }

        node.nodeValue = preparedLine.text;
        state.isTranslated = true;
        return 1;
    }

    function applyCsvTranslationLine(rawLine) {
        const preparedLine = parseCsvTranslationLine(rawLine);
        if (!preparedLine) return 0;
        return applyPreparedTranslation(preparedLine);
    }

    function applyCsvTranslation(csvText) {
        let count = 0;
        csvText.split('\n').forEach(line => {
            count += applyCsvTranslationLine(line);
        });
        return count;
    }

    function flushBatchCsvBuffer(batch, force = false) {
        const segments = batch.pendingCsvBuffer.replace(/\r/g, '').split('\n');
        const lines = force ? segments : segments.slice(0, -1);
        batch.pendingCsvBuffer = force ? '' : (segments[segments.length - 1] || '');

        let appliedCount = 0;

        lines.forEach(line => {
            const preparedLine = parseCsvTranslationLine(line);
            if (!preparedLine || batch.appliedIds.has(preparedLine.id)) return;

            batch.appliedIds.add(preparedLine.id);
            batch.resultLines.push(preparedLine.line);
            appliedCount += applyPreparedTranslation(preparedLine);
        });

        return appliedCount;
    }

    function appendBatchTranslationChunk(batch, chunkText) {
        if (!chunkText) return 0;
        batch.pendingCsvBuffer += chunkText;
        return flushBatchCsvBuffer(batch, false);
    }

    // Restore original text to DOM
    function restoreOriginalText() {
        let count = 0;
        state.originalTexts.forEach((originalText, node) => {
            node.nodeValue = originalText;
            count++;
        });
        state.originalTexts.clear();
        state.isTranslated = false;
        return count;
    }

    // ================= Translation Process =================

    async function startTranslationProcess() {
        if (state.isTranslating) return;
        ensureUI();

        if (state.isTranslated) {
            const count = restoreOriginalText();
            showToast(`已还原原始状态 (${count} 个节点)`, 'success');
            return;
        }
        
        const platform = GM_getValue('gm_platform', CONFIG.defaults.platform);
        const apiKey = GM_getValue(`gm_api_key_${platform}`, '');
        
        if (!apiKey) {
            showToast('请在设置 (Alt+S) 中配置 API 密钥', 'error');
            openSettings();
            return;
        }

        state.isTranslating = true;
        state.isTranslated = false;
        state.originalTexts.clear();
        state.translationProgress = null;
        state.lastProgressUpdateAt = 0;
        state.currentToastId = showToast('正在分析页面结构...', 'loading', 0);

        try {
            const currentUrl = window.location.href;
            const cacheEnabled = GM_getValue('gm_cache_enable', true);
            const nodes = extractTextNodes();

            if (nodes.length === 0) throw new Error('未找到可翻译的文本');

            const entries = generateTranslationEntries(nodes);

            // Try cache first
            if (cacheEnabled) {
                const cachedData = getCache(currentUrl);
                if (cachedData) {
                    state.currentToastId = updateToast(state.currentToastId, '正在从缓存加载...', 'loading');
                    const count = applyCsvTranslation(cachedData);
                    state.currentToastId = updateToast(state.currentToastId, `⚡ 缓存已加载 (${count} 个节点)`, 'success');
                    return;
                }
            }

            const batchNodeCount = getClampedIntegerSetting('gm_batch_node_count', CONFIG.defaults.batchNodeCount, 1, 5000);
            const concurrency = getClampedIntegerSetting('gm_concurrency', CONFIG.defaults.concurrency, 1, 32);
            const batches = createTranslationBatches(entries, batchNodeCount);
            const workerCount = Math.min(concurrency, batches.length);
            const model = GM_getValue('gm_model_input', '').trim()
                || GM_getValue('gm_model', '')
                || CONFIG.platforms[platform].models[0];
            const isDebug = GM_getValue('gm_debug', false);

            if (isDebug) {
                console.log('[Gemini Translator] Translation entries:', entries.length);
                console.log('[Gemini Translator] Batch count:', batches.length, 'Worker count:', workerCount);
            }

            state.translationProgress = createProgressState(entries.length, batches.length, workerCount);
            state.currentToastId = updateToast(
                state.currentToastId,
                `已拆分为 ${batches.length} 批，准备启动 ${workerCount} 个并发请求...`,
                'loading'
            );
            refreshProgressToast(true);

            const resultCsv = await translateAllBatches({
                platform,
                apiKey,
                model,
                prompt: buildTranslationPrompt(),
                batches,
                isDebug
            });

            if (cacheEnabled && resultCsv) {
                setCache(currentUrl, resultCsv);
            }

            state.currentToastId = updateToast(
                state.currentToastId,
                `✅ 翻译完成 (${state.translationProgress.translatedNodes}/${entries.length} 个节点，${batches.length} 批)`,
                'success'
            );
        } catch (e) {
            console.error(e);
            const partialCount = state.originalTexts.size;
            const partialHint = partialCount > 0 ? `；已应用 ${partialCount} 个节点，可再次翻译以还原` : '';
            state.currentToastId = updateToast(state.currentToastId, 'Error: ' + e.message + partialHint, 'error');
        } finally {
            state.isTranslated = state.originalTexts.size > 0;
            state.isTranslating = false;
            state.translationProgress = null;
            state.lastProgressUpdateAt = 0;
        }
    }

    async function translateAllBatches({ platform, apiKey, model, prompt, batches, isDebug }) {
        const errors = [];
        const pendingBatchIndexes = batches.map((_, index) => index);
        const maxBatchRetries = CONFIG.defaults.maxBatchRetries;

        async function worker() {
            while (true) {
                const batchIndex = pendingBatchIndexes.shift();
                if (typeof batchIndex !== 'number') return;

                const batch = batches[batchIndex];
                state.translationProgress.runningBatches++;
                refreshProgressToast(true);

                try {
                    batch.attempts++;
                    await translateSingleBatch({ batch, platform, apiKey, model, prompt, isDebug });
                    state.translationProgress.finishedBatches++;
                } catch (error) {
                    const revertedCount = resetBatchForRetry(batch);
                    if (revertedCount > 0) {
                        state.translationProgress.translatedNodes = Math.max(0, state.translationProgress.translatedNodes - revertedCount);
                    }

                    if (batch.attempts <= maxBatchRetries) {
                        state.translationProgress.retriedBatches++;
                        pendingBatchIndexes.push(batchIndex);

                        if (isDebug) {
                            console.warn(`[Gemini Translator] Batch ${batch.index + 1} failed on attempt ${batch.attempts}, re-queued:`, error);
                        }
                    } else {
                        errors.push({ batchIndex, error, attempts: batch.attempts });
                        state.translationProgress.failedBatches++;

                        if (isDebug) {
                            console.error(`[Gemini Translator] Batch ${batch.index + 1} exhausted retries after ${batch.attempts} attempts:`, error);
                        }
                    }
                } finally {
                    state.translationProgress.runningBatches = Math.max(0, state.translationProgress.runningBatches - 1);
                    refreshProgressToast(true);
                }
            }
        }

        const workers = Array.from({ length: state.translationProgress.workerCount }, () => worker());
        await Promise.all(workers);

        if (errors.length > 0) {
            const firstError = errors[0];
            throw new Error(`第 ${firstError.batchIndex + 1} 批在 ${firstError.attempts} 次尝试后仍然失败: ${firstError.error.message}`);
        }

        return batches
            .map(batch => batch.resultLines.join('\n'))
            .filter(Boolean)
            .join('\n');
    }

    function resetBatchForRetry(batch) {
        let revertedCount = 0;

        batch.appliedIds.forEach(id => {
            const node = state.textNodeMap.get(id);
            const originalText = node ? state.originalTexts.get(node) : null;
            if (!node || originalText == null) return;

            node.nodeValue = originalText;
            revertedCount++;
        });

        batch.pendingCsvBuffer = '';
        batch.resultLines = [];
        batch.appliedIds.clear();

        return revertedCount;
    }

    function translateSingleBatch({ batch, platform, apiKey, model, prompt, isDebug }) {
        return new Promise((resolve, reject) => {
            const requestOptions = createStreamRequestOptions(platform, apiKey, model, prompt, batch.csvInput);
            const parserState = { buffer: '' };
            const textDecoder = new TextDecoder();
            let processedLength = 0;
            let settled = false;
            let streamStarted = false;
            let streamFinalizeTimer = null;

            const finishWithError = (error) => {
                if (settled) return;
                if (streamFinalizeTimer) {
                    clearTimeout(streamFinalizeTimer);
                    streamFinalizeTimer = null;
                }
                settled = true;
                reject(error instanceof Error ? error : new Error(String(error)));
            };

            const finishSuccessfully = () => {
                if (settled) return;
                if (streamFinalizeTimer) {
                    clearTimeout(streamFinalizeTimer);
                    streamFinalizeTimer = null;
                }
                settled = true;
                resolve();
            };

            const finalizeBatch = () => {
                const trailingCount = flushBatchCsvBuffer(batch, true);
                if (trailingCount > 0) {
                    state.translationProgress.translatedNodes += trailingCount;
                    refreshProgressToast(true);
                }

                if (batch.appliedIds.size !== batch.entries.length) {
                    throw new Error(`返回 ${batch.appliedIds.size}/${batch.entries.length} 条结果`);
                }

                if (isDebug) {
                    console.log(`[Gemini Translator] Batch ${batch.index + 1} output:`, batch.resultLines.join('\n'));
                }
            };

            const handlePayload = (payload) => {
                const chunkText = extractStreamTextFromPayload(payload, platform);
                const appliedCount = appendBatchTranslationChunk(batch, chunkText);
                if (appliedCount > 0) {
                    state.translationProgress.translatedNodes += appliedCount;
                    refreshProgressToast();
                }
            };

            const processIncomingText = (responseBody, force = false) => {
                if (!responseBody) return;
                if (responseBody.length < processedLength) {
                    processedLength = 0;
                }

                const delta = responseBody.slice(processedLength);
                processedLength = responseBody.length;

                if (delta) {
                    consumeSSEPayload(delta, parserState, handlePayload);
                }

                if (force) {
                    flushSSEBuffer(parserState, handlePayload);
                }
            };

            const processIncomingChunk = (chunk, force = false) => {
                if (chunk == null) {
                    if (force) {
                        flushSSEBuffer(parserState, handlePayload);
                    }
                    return;
                }

                if (typeof chunk === 'string') {
                    consumeSSEPayload(chunk, parserState, handlePayload);
                } else {
                    consumeSSEPayload(textDecoder.decode(chunk, { stream: !force }), parserState, handlePayload);
                }

                if (force) {
                    flushSSEBuffer(parserState, handlePayload);
                }
            };

            const consumeReadableStream = async (stream) => {
                if (!stream) {
                    throw new Error('Tampermonkey 未提供流对象');
                }

                if (typeof stream.getReader === 'function') {
                    const reader = stream.getReader();
                    try {
                        while (true) {
                            const { done, value } = await reader.read();
                            if (done) break;
                            processIncomingChunk(value, false);
                        }
                        processIncomingChunk(null, true);
                        return;
                    } finally {
                        if (typeof reader.releaseLock === 'function') {
                            reader.releaseLock();
                        }
                    }
                }

                if (typeof stream.read === 'function') {
                    while (true) {
                        const result = await stream.read();
                        if (!result || result.done) break;
                        processIncomingChunk(result.value, false);
                    }
                    processIncomingChunk(null, true);
                    return;
                }

                throw new Error('无法识别的流对象类型');
            };

            const finalizeFromLoadFallback = (res) => {
                if (settled) return;

                try {
                    const fallbackText = res.responseText || (typeof res.response === 'string' ? res.response : '');
                    if (fallbackText) {
                        processIncomingText(fallbackText, true);
                    } else {
                        processIncomingChunk(null, true);
                    }

                    if (isDebug) {
                        console.log(`[Gemini Translator] Batch ${batch.index + 1} finalized from onload fallback.`);
                    }

                    finalizeBatch();
                    finishSuccessfully();
                } catch (error) {
                    finishWithError(error);
                }
            };

            GM_xmlhttpRequest({
                ...requestOptions,
                onloadstart: (res) => {
                    if (settled || !res?.response) return;

                    streamStarted = true;
                    (async () => {
                        try {
                            if (isHttpErrorStatus(res.status)) {
                                throw new Error(getResponseErrorMessage(res));
                            }

                            await consumeReadableStream(res.response);
                            finalizeBatch();
                            finishSuccessfully();
                        } catch (error) {
                            finishWithError(error);
                        }
                    })();
                },
                onprogress: (res) => {
                    if (settled || streamStarted) return;
                    try {
                        processIncomingText(res.responseText || res.response || '', false);
                    } catch (error) {
                        finishWithError(error);
                    }
                },
                onload: (res) => {
                    if (settled) return;

                    try {
                        if (isHttpErrorStatus(res.status)) {
                            throw new Error(getResponseErrorMessage(res));
                        }

                        if (streamStarted) {
                            if (streamFinalizeTimer) {
                                clearTimeout(streamFinalizeTimer);
                            }
                            streamFinalizeTimer = setTimeout(() => finalizeFromLoadFallback(res), 150);
                            return;
                        }

                        processIncomingText(res.responseText || res.response || '', true);
                        finalizeBatch();
                        finishSuccessfully();
                    } catch (error) {
                        finishWithError(error);
                    }
                },
                onerror: (err) => finishWithError(new Error(getNetworkErrorMessage(err)))
            });
        });
    }

    function createStreamRequestOptions(platform, apiKey, model, prompt, csvInput) {
        if (platform === 'gemini') {
            return {
                method: 'POST',
                url: `${CONFIG.platforms.gemini.endpoint}/${model}:streamGenerateContent?alt=sse`,
                responseType: 'stream',
                headers: {
                    'Content-Type': 'application/json',
                    'x-goog-api-key': apiKey
                },
                data: JSON.stringify({
                    system_instruction: { parts: [{ text: prompt }] },
                    contents: [{ parts: [{ text: csvInput }] }]
                })
            };
        }

        const reasoningEnabled = GM_getValue('gm_reasoning_enable', true);
        return {
            method: 'POST',
            url: CONFIG.platforms.openrouter.endpoint,
            responseType: 'stream',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${apiKey}`
            },
            data: JSON.stringify({
                model,
                messages: [
                    { role: 'system', content: prompt },
                    { role: 'user', content: csvInput }
                ],
                reasoning: {
                    enabled: reasoningEnabled
                },
                stream: true
            })
        };
    }

    function consumeSSEPayload(chunk, parserState, onPayload) {
        parserState.buffer += chunk.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
        const events = parserState.buffer.split('\n\n');
        parserState.buffer = events.pop() || '';
        events.forEach(event => dispatchSSEEvent(event, onPayload));
    }

    function flushSSEBuffer(parserState, onPayload) {
        if (!parserState.buffer.trim()) {
            parserState.buffer = '';
            return;
        }

        dispatchSSEEvent(parserState.buffer, onPayload);
        parserState.buffer = '';
    }

    function dispatchSSEEvent(rawEvent, onPayload) {
        if (!rawEvent.trim()) return;

        const dataLines = rawEvent
            .split('\n')
            .map(line => line.trimEnd())
            .filter(line => line.startsWith('data:'))
            .map(line => line.slice(5).trimStart());

        if (dataLines.length === 0) return;

        const payload = dataLines.join('\n').trim();
        if (!payload || payload === '[DONE]') return;
        onPayload(payload);
    }

    function extractStreamTextFromPayload(payload, platform) {
        const data = JSON.parse(payload);

        if (data.error?.message) {
            throw new Error(data.error.message);
        }

        if (platform === 'gemini') {
            return (data.candidates || [])
                .flatMap(candidate => candidate.content?.parts || [])
                .map(part => part.text || '')
                .join('');
        }

        const hasErrorFinishReason = (data.choices || []).some(choice => choice.finish_reason === 'error');
        if (hasErrorFinishReason) {
            throw new Error('OpenRouter 流式响应中断');
        }

        return (data.choices || [])
            .map(choice => choice.delta?.content || '')
            .join('');
    }

    function getResponseErrorMessage(res) {
        try {
            const err = JSON.parse(res.responseText || '{}');
            return err.error?.message || `Status ${res.status}`;
        } catch {
            return `Status ${res.status}`;
        }
    }

    function isHttpErrorStatus(status) {
        return typeof status === 'number' && status >= 400;
    }

    function getNetworkErrorMessage(err) {
        return err?.error || err?.statusText || '网络错误';
    }

    // ================= UI Components =================

    function createUI() {
        // Use a custom tag name to avoid interfering with CSS selectors like :last-of-type on common tags
        const div = document.createElement('gm-translator');
        div.id = 'gm-translator-container';
        div.innerHTML = `
            <div id="gm-settings-overlay">
                <div id="gm-settings-panel">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <h3 style="color:#fff; margin:0; font-size:18px;">Gemini 翻译器 V7.1.0</h3>
                    </div>
                    
                    <div>
                        <label class="gm-label">平台</label>
                        <select id="gm-platform" class="gm-select">
                            <option value="gemini">Google Gemini</option>
                            <option value="openrouter">OpenRouter</option>
                        </select>
                    </div>
                    
                    <div id="gm-api-key-container">
                        <label class="gm-label" id="gm-key-label">API 密钥</label>
                        <input type="text" id="gm-key" class="gm-input gm-input-secret" placeholder="粘贴 API 密钥" spellcheck="false">
                    </div>
                    
                    <div>
                        <label class="gm-label">模型</label>
                        <select id="gm-model" class="gm-select" style="margin-bottom:8px"></select>
                        <input type="text" id="gm-model-input" class="gm-input" placeholder="或手动输入模型代码">
                    </div>

                    <div class="gm-checkbox-group">
                        <label class="gm-checkbox-label">
                            <input type="checkbox" id="gm-cache"> 启用本地缓存
                        </label>
                        <label class="gm-checkbox-label">
                            <input type="checkbox" id="gm-debug"> 调试模式
                        </label>
                         <label class="gm-checkbox-label gm-hidden" id="gm-reasoning-container">
                            <input type="checkbox" id="gm-reasoning"> 启用推理 (Reasoning)
                        </label>
                    </div>

                    <div style="display:grid; grid-template-columns: 1fr 1fr; gap:12px;">
                        <div>
                            <label class="gm-label">批大小 (节点)</label>
                            <input type="number" id="gm-batch-size" class="gm-input" min="1" max="5000" step="1" placeholder="500">
                        </div>
                        <div>
                            <label class="gm-label">并发线程</label>
                            <input type="number" id="gm-concurrency" class="gm-input" min="1" max="32" step="1" placeholder="8">
                        </div>
                    </div>

                    <div style="display:grid; grid-template-columns: 1fr 1fr; gap:12px;">
                        <div>
                            <label class="gm-label">URL 相似度 (%)</label>
                            <input type="number" id="gm-url-similarity" class="gm-input" min="50" max="100" step="1" placeholder="95">
                        </div>
                        <div>
                             <label class="gm-label">缓存有效期</label>
                            <select id="gm-cache-ttl" class="gm-select">
                                <option value="86400000">1 天</option>
                                <option value="259200000">3 天</option>
                                <option value="604800000">7 天</option>
                                <option value="2592000000">30 天</option>
                                <option value="0">永不过期</option>
                            </select>
                        </div>
                    </div>
                    
                    <div>
                        <label class="gm-label">缓存大小限制</label>
                        <select id="gm-max-cache-size" class="gm-select">
                            <option value="5242880">5 MB</option>
                            <option value="10485760">10 MB</option>
                            <option value="20971520">20 MB</option>
                            <option value="52428800">50 MB</option>
                            <option value="104857600">100 MB</option>
                            <option value="524288000">500 MB</option>
                            <option value="1073741824">1 GB</option>
                        </select>
                    </div>

                    <div id="gm-cache-stats">
                        已用: <span id="gm-cache-used">--</span> / <span id="gm-cache-limit">--</span>
                        (<span id="gm-cache-count">0</span> 个页面)
                    </div>

                    <div style="display:flex; justify-content:space-between; margin-top:10px;">
                        <div style="display:flex; gap:8px;">
                            <button id="gm-clear-current" class="gm-btn gm-btn-danger">清除当前</button>
                            <button id="gm-clear-all" class="gm-btn gm-btn-danger">清除全部</button>
                        </div>
                        <div class="gm-btn-group">
                            <button id="gm-close" class="gm-btn gm-btn-secondary">取消</button>
                            <button id="gm-save" class="gm-btn gm-btn-primary">保存</button>
                        </div>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(div);

        // Platform selection handler
        document.getElementById('gm-platform').onchange = updatePlatformUI;
        
        document.getElementById('gm-save').onclick = () => {
            const platform = document.getElementById('gm-platform').value;
            
            // Save all API keys regardless of selected platform
            GM_setValue(`gm_api_key_${platform}`, document.getElementById('gm-key').value.trim());
            
            GM_setValue('gm_platform', platform);
            GM_setValue('gm_model', document.getElementById('gm-model').value);
            GM_setValue('gm_model_input', document.getElementById('gm-model-input').value.trim());
            GM_setValue('gm_cache_enable', document.getElementById('gm-cache').checked);
            GM_setValue('gm_debug', document.getElementById('gm-debug').checked);
            GM_setValue('gm_reasoning_enable', document.getElementById('gm-reasoning').checked);

            const similarityInput = parseFloat(document.getElementById('gm-url-similarity').value);
            const similarity = Number.isNaN(similarityInput)
                ? CONFIG.defaults.urlSimilarityThreshold
                : Math.min(Math.max(similarityInput / 100, 0), 1);
            const batchNodeCount = readClampedIntegerFromInput('gm-batch-size', CONFIG.defaults.batchNodeCount, 1, 5000);
            const concurrency = readClampedIntegerFromInput('gm-concurrency', CONFIG.defaults.concurrency, 1, 32);

            GM_setValue('gm_url_similarity_threshold', similarity);
            GM_setValue('gm_batch_node_count', batchNodeCount);
            GM_setValue('gm_concurrency', concurrency);

            GM_setValue('gm_max_cache_size', parseInt(document.getElementById('gm-max-cache-size').value));
            GM_setValue('gm_cache_ttl', parseInt(document.getElementById('gm-cache-ttl').value));
            closeSettings();
            showToast('设置已保存', 'success');
        };
        document.getElementById('gm-close').onclick = closeSettings;
        document.getElementById('gm-settings-overlay').onclick = (e) => {
            if(e.target.id === 'gm-settings-overlay') closeSettings();
        };
        document.getElementById('gm-clear-current').onclick = () => {
            clearCurrentPageCache();
            updateCacheStats();
            showToast('当前页面缓存已清除', 'success');
        };
        document.getElementById('gm-clear-all').onclick = () => {
            if (confirm('要清除所有缓存吗?')) {
                clearAllCache();
                updateCacheStats();
                showToast('所有缓存已清除', 'success');
            }
        };
    }

    // Update UI based on selected platform
    function updatePlatformUI() {
        const platform = document.getElementById('gm-platform').value;
        const modelSelect = document.getElementById('gm-model');
        const keyInput = document.getElementById('gm-key');
        
        // Load saved API key for this platform
        keyInput.value = GM_getValue(`gm_api_key_${platform}`, '');
        
        // Update model dropdown
        modelSelect.innerHTML = '';
        const models = CONFIG.platforms[platform].models;
        models.forEach(model => {
            const option = document.createElement('option');
            option.value = model;
            option.textContent = model;
            modelSelect.appendChild(option);
        });
        
        // Restore saved model if it exists in the list
        const savedModel = GM_getValue('gm_model', '');
        if (models.includes(savedModel)) {
            modelSelect.value = savedModel;
        }

        // Toggle Reasoning option
        const reasoningContainer = document.getElementById('gm-reasoning-container');
        if (reasoningContainer) {
            if (platform === 'openrouter') {
                reasoningContainer.classList.remove('gm-hidden');
            } else {
                reasoningContainer.classList.add('gm-hidden');
            }
        }
    }

    // Update cache statistics display
    function updateCacheStats() {
        const stats = getCacheStats();
        const maxSize = GM_getValue('gm_max_cache_size', CONFIG.defaults.maxCacheSize);
        
        document.getElementById('gm-cache-used').textContent = formatBytes(stats.totalSize);
        document.getElementById('gm-cache-limit').textContent = formatBytes(maxSize);
        document.getElementById('gm-cache-count').textContent = stats.count;
    }

    function openSettings() {
        ensureUI();
        const platform = GM_getValue('gm_platform', CONFIG.defaults.platform);
        document.getElementById('gm-platform').value = platform;
        
        updatePlatformUI();
        
        document.getElementById('gm-model-input').value = GM_getValue('gm_model_input', '');
        document.getElementById('gm-cache').checked = GM_getValue('gm_cache_enable', true);
        document.getElementById('gm-debug').checked = GM_getValue('gm_debug', false);
        document.getElementById('gm-reasoning').checked = GM_getValue('gm_reasoning_enable', true);
        document.getElementById('gm-batch-size').value = getClampedIntegerSetting('gm_batch_node_count', CONFIG.defaults.batchNodeCount, 1, 5000);
        document.getElementById('gm-concurrency').value = getClampedIntegerSetting('gm_concurrency', CONFIG.defaults.concurrency, 1, 32);
        const similarity = getUrlSimilarityThreshold();
        document.getElementById('gm-url-similarity').value = Math.round(similarity * 100);
        document.getElementById('gm-max-cache-size').value = GM_getValue('gm_max_cache_size', CONFIG.defaults.maxCacheSize);
        document.getElementById('gm-cache-ttl').value = GM_getValue('gm_cache_ttl', CONFIG.defaults.cacheTTL);
        
        updateCacheStats();
        document.getElementById('gm-settings-overlay').classList.add('open');
    }
    
    function closeSettings() {
        document.getElementById('gm-settings-overlay').classList.remove('open');
    }

    // ================= Toast Management =================
    
    const activeToasts = new Map();

    function showToast(text, type = 'info', duration = 3000) {
        ensureUI();
        const container = document.getElementById('gm-translator-container');
        const toast = document.createElement('div');
        toast.className = 'gm-toast';
        toast.innerHTML = getToastContent(text, type);
        container.appendChild(toast);
        
        requestAnimationFrame(() => toast.classList.add('visible'));
        
        const id = Date.now() + Math.random();
        activeToasts.set(id, toast);
        
        if (duration > 0) setTimeout(() => removeToast(id), duration);
        return id;
    }

    function updateToast(id, text, type) {
        const toast = activeToasts.get(id);
        if (!toast) return showToast(text, type);
        
        toast.innerHTML = getToastContent(text, type);
        
        // Auto-destroy when not loading
        if (type !== 'loading') {
            setTimeout(() => removeToast(id), 3000);
        }

        return id;
    }

    function getToastContent(text, type) {
        let icon = '';
        if (type === 'loading') icon = '<div class="gm-spinner"></div>';
        else if (type === 'success') icon = '<span style="color:#4ade80; font-size:16px;">✓</span>';
        else if (type === 'error') icon = '<span style="color:#ef4444; font-size:16px;">✕</span>';
        return `${icon}<span>${text}</span>`;
    }

    function removeToast(id) {
        const toast = activeToasts.get(id);
        if (toast) {
            toast.classList.remove('visible');
            setTimeout(() => {
                if (toast.parentNode) toast.parentNode.removeChild(toast);
                activeToasts.delete(id);
            }, 300);
        }
    }

    if (isExcludedContext()) {
        console.log('[Gemini Translator] Disabled on this page to avoid interference or protect sensitive data.');
        return;
    }

    init();
})();

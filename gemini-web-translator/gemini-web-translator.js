// ==UserScript==
// @name         Gemini Webpage Translator
// @namespace    http://tampermonkey.net/
// @version      7.0
// @description  Translate webpages using Gemini or OpenRouter API.
// @author       NordLandeW
// @match        *://*/*
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
                    'gemini-3.0-pro-preview',
                    'gemini-2.0-flash-exp',
                    'gemini-1.5-pro',
                    'gemini-1.5-flash'
                ]
            },
            openrouter: {
                name: 'OpenRouter',
                endpoint: 'https://openrouter.ai/api/v1/chat/completions',
                models: [
                    'google/gemini-2.5-pro',
                    'deepseek/deepseek-v3.2'
                ]
            }
        },
        cachePrefix: 'gm_cache_v7_',
        cacheMetaKey: 'gm_cache_meta',
        defaults: {
            platform: 'gemini',
            maxCacheSize: 10 * 1024 * 1024, // 10MB
            cacheTTL: 7 * 24 * 60 * 60 * 1000, // 7 days
            urlSimilarityThreshold: 0.95
        }
    };

    const state = {
        isTranslating: false,
        textNodeMap: new Map(),
        currentToastId: null
    };

    // ================= 样式表 =================
    const STYLES = `
        :root { --gm-primary: #8ab4f8; --gm-bg: #202124; --gm-surface: #303134; --gm-text: #e8eaed; --gm-border: #5f6368; }
        #gm-translator-container { font-family: 'Segoe UI', system-ui, sans-serif; z-index: 2147483647; position: fixed; top: 0; left: 0; color-scheme: dark; }
        
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
            max-width: 320px;
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

    // ================= Initialization =================

    function init() {
        const style = document.createElement('style');
        style.textContent = STYLES;
        document.head.appendChild(style);
        
        createUI();
        
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

    // Generate CSV and node mapping
    function generateCsvAndMap(nodes) {
        state.textNodeMap.clear();
        let csv = "id,text\n";
        const tagCounters = {};

        nodes.forEach((node, index) => {
            const tag = node.parentElement.tagName.toUpperCase();
            if (!tagCounters[tag]) tagCounters[tag] = 0;
            const id = `${tag}_${tagCounters[tag]++}_${index}`;
            
            state.textNodeMap.set(id, node);
            
            // CSV escape: double quotes and newlines
            const safeText = node.nodeValue.replace(/"/g, '""').replace(/[\r\n]+/g, ' ');
            csv += `${id},"${safeText}"\n`;
        });
        return csv;
    }

    // Apply CSV translation results to DOM
    function applyCsvTranslation(csvText) {
        const regex = /^([^,]+),"(.*)"$/;
        const lines = csvText.split('\n');
        let count = 0;

        lines.forEach(line => {
            line = line.trim();
            if (!line) return;
            
            const match = line.match(regex);
            if (match) {
                const id = match[1];
                const text = match[2].replace(/""/g, '"');
                
                const node = state.textNodeMap.get(id);
                if (node) {
                    node.nodeValue = text;
                    count++;
                }
            }
        });
        return count;
    }

    // ================= Translation Process =================

    async function startTranslationProcess() {
        if (state.isTranslating) return;
        
        const platform = GM_getValue('gm_platform', CONFIG.defaults.platform);
        const apiKey = GM_getValue(`gm_api_key_${platform}`, '');
        
        if (!apiKey) {
            showToast('请在设置 (Alt+S) 中配置 API 密钥', 'error');
            openSettings();
            return;
        }
    
        state.isTranslating = true;
        state.currentToastId = showToast('正在分析页面结构...', 'loading', 0);
    
        try {
            const currentUrl = window.location.href;
            const cacheEnabled = GM_getValue('gm_cache_enable', true);
            const nodes = extractTextNodes();
            
            if (nodes.length === 0) throw new Error('未找到可翻译的文本');
            
            const csvInput = generateCsvAndMap(nodes);
    
            // Try cache first
            if (cacheEnabled) {
                const cachedData = getCache(currentUrl);
                if (cachedData) {
                    updateToast(state.currentToastId, '正在从缓存加载...', 'loading');
                    const count = applyCsvTranslation(cachedData);
                    updateToast(state.currentToastId, `⚡ 缓存已加载 (${count} 个节点)`, 'success');
                    state.isTranslating = false;
                    return;
                }
            }
    
            updateToast(state.currentToastId, `正在翻译 ${nodes.length} 个文本片段...`, 'loading');
    
            const model = GM_getValue('gm_model_input', '') || GM_getValue('gm_model', '');
            const isDebug = GM_getValue('gm_debug', false);
    
            if (isDebug) console.log('CSV Input:', csvInput);
    
            const prompt = `You are a CSV translator.
Input format: id,"text content"
Task: Translate "text content" to Simplified Chinese.
Rules:
1. Keep "id" exactly the same.
2. Do NOT translate content inside HTML-like tags if any exist, but translate the text around them.
3. Output valid CSV: id,"translated_text".
4. If text is already Chinese, keep it as is.
5. Escape double quotes with "".`;

            if (platform === 'gemini') {
                callGeminiAPI(apiKey, model, prompt, csvInput, currentUrl, cacheEnabled, isDebug);
            } else if (platform === 'openrouter') {
                callOpenRouterAPI(apiKey, model, prompt, csvInput, currentUrl, cacheEnabled, isDebug);
            }
    
        } catch (e) {
            console.error(e);
            updateToast(state.currentToastId, 'Error: ' + e.message, 'error');
            state.isTranslating = false;
        }
    }

    function callGeminiAPI(apiKey, model, prompt, csvInput, currentUrl, cacheEnabled, isDebug) {
        GM_xmlhttpRequest({
            method: "POST",
            url: `${CONFIG.platforms.gemini.endpoint}/${model}:generateContent`,
            headers: {
                "Content-Type": "application/json",
                "x-goog-api-key": apiKey
            },
            data: JSON.stringify({
                system_instruction: { parts: { text: prompt } },
                contents: [{ parts: [{ text: csvInput }] }]
            }),
            onload: (res) => handleAPIResponse(res, currentUrl, cacheEnabled, isDebug, 'gemini'),
            onerror: (err) => handleAPIError(err)
        });
    }

    function callOpenRouterAPI(apiKey, model, prompt, csvInput, currentUrl, cacheEnabled, isDebug) {
        const reasoningEnabled = GM_getValue('gm_reasoning_enable', true);
        GM_xmlhttpRequest({
            method: "POST",
            url: CONFIG.platforms.openrouter.endpoint,
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${apiKey}`
            },
            data: JSON.stringify({
                model: model,
                messages: [
                    { role: "system", content: prompt },
                    { role: "user", content: csvInput }
                ],
                reasoning: {
                    enabled: reasoningEnabled
                }
            }),
            onload: (res) => handleAPIResponse(res, currentUrl, cacheEnabled, isDebug, 'openrouter'),
            onerror: (err) => handleAPIError(err)
        });
    }

    function handleAPIResponse(res, currentUrl, cacheEnabled, isDebug, platform) {
        try {
            if (res.status !== 200) {
                handleError(res);
                return;
            }
            
            const data = JSON.parse(res.responseText || '{}');
            let resultCsv;
            
            if (platform === 'gemini') {
                resultCsv = data.candidates?.[0]?.content?.parts?.[0]?.text;
            } else if (platform === 'openrouter') {
                resultCsv = data.choices?.[0]?.message?.content;
            }

            if (!resultCsv) {
                throw new Error('API 返回内容为空');
            }

            resultCsv = resultCsv
                .replace(/^```csv\s*/i, '')
                .replace(/^```\s*/i, '')
                .replace(/\s*```$/, '');

            if (isDebug) console.log('CSV Output:', resultCsv);

            const count = applyCsvTranslation(resultCsv);

            if (cacheEnabled) {
                setCache(currentUrl, resultCsv);
            }

            updateToast(state.currentToastId, `✅ 翻译完成 (${count} 个节点)`, 'success');
        } catch (e) {
            console.error(e);
            updateToast(state.currentToastId, 'Error: ' + e.message, 'error');
        } finally {
            state.isTranslating = false;
        }
    }

    function handleAPIError(err) {
        console.error(err);
        updateToast(state.currentToastId, '网络错误', 'error');
        state.isTranslating = false;
    }

    function handleError(res) {
        const err = JSON.parse(res.responseText || '{}');
        const msg = err.error?.message || `Status ${res.status}`;
        updateToast(state.currentToastId, 'API 请求失败: ' + msg, 'error');
        state.isTranslating = false;
    }

    // ================= UI Components =================

    function createUI() {
        const div = document.createElement('div');
        div.id = 'gm-translator-container';
        div.innerHTML = `
            <div id="gm-settings-overlay">
                <div id="gm-settings-panel">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <h3 style="color:#fff; margin:0; font-size:18px;">Gemini 翻译器 V7.0</h3>
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
            GM_setValue('gm_url_similarity_threshold', similarity);

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
        const platform = GM_getValue('gm_platform', CONFIG.defaults.platform);
        document.getElementById('gm-platform').value = platform;
        
        updatePlatformUI();
        
        document.getElementById('gm-model-input').value = GM_getValue('gm_model_input', '');
        document.getElementById('gm-cache').checked = GM_getValue('gm_cache_enable', true);
        document.getElementById('gm-debug').checked = GM_getValue('gm_debug', false);
        document.getElementById('gm-reasoning').checked = GM_getValue('gm_reasoning_enable', true);
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

    init();
})();
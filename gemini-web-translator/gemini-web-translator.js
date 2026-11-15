// ==UserScript==
// @name         Gemini Webpage Translator
// @namespace    http://tampermonkey.net/
// @version      7.4.0
// @description  Translate webpages using Gemini, OpenRouter, or OpenAI (Chat Completions / Responses).
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
// @grant        GM_info
// @sandbox      DOM
// @connect      generativelanguage.googleapis.com
// @connect      openrouter.ai
// @connect      api.openai.com
// @connect      *
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
                    'gemini-3.1-pro-preview',
                    'gemini-3.8-flash',
                    'gemini-4-argon',
                    'gemini-3.5-flash-lite',
                ]
            },
            openrouter: {
                name: 'OpenRouter',
                endpoint: 'https://openrouter.ai/api/v1/chat/completions',
                models: [
                    'google/gemini-3.1-pro-preview',
                    'google/gemini-3.8-flash',
                    'google/gemini-4-argon',
                    'google/gemini-3.5-flash-lite',
                    'openai/gpt-6-luna',
                    'openai/gpt-6.1-sol',
                    'openai/gpt-6.1-terra',
                    'openai/gpt-6-astra',
                    'deepseek/deepseek-v4-flash',
                    'deepseek/deepseek-v4-pro',
                ]
            },
            openai: {
                name: 'OpenAI / 兼容 API',
                endpoint: 'https://api.openai.com/v1',
                models: [
                    'gpt-6-luna',
                    'gpt-6.1-sol',
                    'gpt-6.1-terra',
                    'gpt-6-astra',
                ]
            }
        },
        openaiServiceTiers: ['', 'auto', 'default', 'flex', 'priority', 'fast', 'scale', 'ultrafast'],
        // Model IDs and effort support verified against provider docs on 2026-10-29.
        reasoningProfiles: {
            'gemini-4-argon': ['low', 'medium', 'high'],
            'gemini-3.8-flash': ['low', 'medium', 'high'],
            'gemini-3.1-pro-preview': ['low', 'medium', 'high'],
            'gemini-3.5-flash-lite': ['minimal', 'low', 'medium', 'high'],
            'gemini-3.6-flash': ['minimal', 'low', 'medium', 'high'],
            'gemini-3.1-flash-lite': ['minimal', 'low', 'medium', 'high'],
            'gemini-3-flash-preview': ['minimal', 'low', 'medium', 'high'],
            'gemini-3-pro-preview': ['low', 'high'],
            'gpt-6-astra': ['low', 'medium', 'high', 'xhigh', 'max'],
            'gpt-6.1-sol': ['low', 'medium', 'high', 'xhigh', 'max'],
            'gpt-6.1-terra': ['none', 'low', 'medium', 'high', 'xhigh', 'max'],
            'gpt-6-luna': ['none', 'low', 'medium', 'high', 'xhigh', 'max']
        },
        reasoningEffortLabels: {
            '': '默认（不指定强度）',
            none: 'None（关闭推理）',
            minimal: 'Minimal（极低）',
            low: 'Low（低）',
            medium: 'Medium（中）',
            high: 'High（高）',
            xhigh: 'XHigh（极高）',
            max: 'Max（最高）'
        },
        cachePrefix: 'gm_cache_v8_',
        cacheVersion: 1,
        cacheMetaKey: 'gm_cache_meta',
        finishNodeId: '__FINISH__',
        commonTargetLanguages: [
            'Simplified Chinese',
            'Traditional Chinese',
            'English',
            'Japanese',
            'Korean',
            'French',
            'German',
            'Spanish',
            'Portuguese',
            'Russian',
            'Italian',
            'Dutch',
            'Vietnamese',
            'Thai',
            'Indonesian',
            'Arabic'
        ],
        defaults: {
            platform: 'gemini',
            openaiProtocol: 'chat',
            openaiServiceTier: 'auto',
            maxCacheSize: 10 * 1024 * 1024, // 10MB
            cacheTTL: 7 * 24 * 60 * 60 * 1000, // 7 days
            urlSimilarityThreshold: 0.95,
            targetLanguage: 'Simplified Chinese',
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
        sourceTexts: new Map(),
        lastAppliedTexts: new Map(),
        retryWaits: new Set(),
        retryContext: null,
        currentToastId: null,
        translationProgress: null,
        lastProgressUpdateAt: 0,
        activeTranslationRunId: 0,
        activeRequests: new Set(),
        settingsPlatform: null,
        settingsDrafts: new Map()
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
            background: #2b2c30; color: var(--gm-text); border: 1px solid rgba(255,255,255,0.1);
            width: 420px; max-width: calc(100vw - 32px); max-height: 90vh; overflow-y: auto;
            box-sizing: border-box; border-radius: 16px; padding: 32px;
            box-shadow: 0 24px 48px rgba(0,0,0,0.5);
            transform: scale(0.92) translateY(10px); transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
            display: flex; flex-direction: column; gap: 16px;
        }
        #gm-settings-overlay.open #gm-settings-panel { transform: scale(1) translateY(0); }
        #gm-settings-panel > * { flex-shrink: 0; }

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
            // Query parameters, fragments and trailing slashes can identify different content.
            return url.href;
        } catch {
            return String(rawUrl);
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

    function normalizeTargetLanguage(language) {
        const value = String(language || '').trim().replace(/\s+/g, ' ');
        return value || CONFIG.defaults.targetLanguage;
    }

    function getTargetLanguage() {
        return normalizeTargetLanguage(GM_getValue('gm_target_language', CONFIG.defaults.targetLanguage));
    }

    function getPlatformModelSettings(platform) {
        // Keep existing model settings when upgrading from the shared model keys.
        const useLegacy = platform === GM_getValue('gm_platform', CONFIG.defaults.platform);
        return {
            model: GM_getValue(`gm_model_${platform}`, useLegacy ? GM_getValue('gm_model', '') : ''),
            customModel: GM_getValue(`gm_model_input_${platform}`, useLegacy ? GM_getValue('gm_model_input', '') : '')
        };
    }

    function getOpenAIProtocol() {
        const protocol = GM_getValue('gm_openai_protocol', CONFIG.defaults.openaiProtocol);
        return protocol === 'responses' ? protocol : CONFIG.defaults.openaiProtocol;
    }

    function getReasoningEfforts(platform, model) {
        const modelId = String(model).replace(/^(?:google|openai)\//, '').replace(/:[^:]+$/, '');
        const profileId = Object.keys(CONFIG.reasoningProfiles).find(id =>
            modelId === id || new RegExp(`^${id.replace(/\./g, '\\.')}-(?:\\d{8}|\\d{4}-\\d{2}-\\d{2})$`).test(modelId));
        if (profileId) return CONFIG.reasoningProfiles[profileId];
        if (platform === 'gemini') {
            // Gemini 2.5 uses token budgets rather than thinkingLevel.
            if (modelId.startsWith('gemini-2.')) return [];
            return ['low', 'medium', 'high'];
        }
        if (modelId.startsWith('gpt-4')) return [];
        // Compatible APIs and custom models may expose the complete effort enum.
        return ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'];
    }

    function getSavedReasoningEffort(platform) {
        const legacy = platform === 'openrouter' && !GM_getValue('gm_reasoning_enable', true) ? 'none' : '';
        return GM_getValue(`gm_reasoning_effort_${platform}`, legacy);
    }

    function getReasoningEffort(platform, model) {
        const effort = getSavedReasoningEffort(platform);
        return getReasoningEfforts(platform, model).includes(effort) ? effort : '';
    }

    function getOpenAIServiceTier(protocol) {
        const tier = GM_getValue('gm_openai_service_tier', CONFIG.defaults.openaiServiceTier);
        if (!CONFIG.openaiServiceTiers.includes(tier) || (tier === 'ultrafast' && protocol !== 'responses')) {
            return CONFIG.defaults.openaiServiceTier;
        }
        return tier;
    }

    function normalizeOpenAIBaseUrl(value) {
        const url = new URL(String(value || '').trim() || CONFIG.platforms.openai.endpoint);
        if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
            throw new Error('API Base URL 必须是 HTTP(S) 地址，且不含认证信息、查询参数或片段');
        }
        url.pathname = url.pathname.replace(/\/+$/, '').replace(/\/(?:chat\/completions|responses)$/i, '') || '/v1';
        return url.href.replace(/\/+$/, '');
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

    // URLs select candidates only. Original text and context authorize reuse.
    function findCacheCandidates(rawUrl, meta, targetLanguage = getTargetLanguage(), profile) {
        const normalizedUrl = normalizeUrl(rawUrl);
        const language = normalizeTargetLanguage(targetLanguage);
        const threshold = getUrlSimilarityThreshold();
        let origin;
        try { origin = new URL(normalizedUrl).origin; } catch { return []; }
        return Object.entries(meta).flatMap(([key, entry]) => {
            if (entry.targetLanguage !== language || (profile !== undefined && entry.profile !== profile)) return [];
            try { if (new URL(entry.normalizedUrl).origin !== origin) return []; } catch { return []; }
            const score = stringSimilarity(normalizedUrl, entry.normalizedUrl);
            return score >= threshold ? [{ key, entry, score }] : [];
        }).sort((a, b) => b.score - a.score || b.entry.timestamp - a.entry.timestamp);
    }

    function generateCacheKey(url, targetLanguage = getTargetLanguage(), profile = '') {
        const source = JSON.stringify([normalizeTargetLanguage(targetLanguage), url, profile]);
        let hash = 0;
        for (let i = 0; i < source.length; i++) hash = ((hash << 5) - hash + source.charCodeAt(i)) | 0;
        // Hash collisions may replace an entry, but never bypass metadata/content validation.
        return CONFIG.cachePrefix + (hash >>> 0).toString(36);
    }

    function getCacheMeta() {
        let meta;
        try { meta = JSON.parse(GM_getValue(CONFIG.cacheMetaKey, '{}')); } catch { return {}; }
        if (!meta || typeof meta !== 'object' || Array.isArray(meta)) return {};
        let changed = false;
        for (const [key, entry] of Object.entries(meta)) {
            if (!key.startsWith(CONFIG.cachePrefix) || !entry || entry.version !== CONFIG.cacheVersion
                || typeof entry.normalizedUrl !== 'string' || typeof entry.profile !== 'string'
                || !Number.isFinite(entry.size) || entry.size < 0 || !Number.isFinite(entry.timestamp)) {
                // Positional v7 records have no original-text evidence and cannot be migrated.
                if (/^gm_cache_v\d+_/.test(key)) GM_deleteValue(key);
                delete meta[key];
                changed = true;
            }
        }
        if (changed) saveCacheMeta(meta);
        return meta;
    }

    function saveCacheMeta(meta) {
        GM_setValue(CONFIG.cacheMetaKey, JSON.stringify(meta));
    }

    function getCache(rawUrl, targetLanguage = getTargetLanguage(), profile = '') {
        const meta = getCacheMeta();
        const records = new Map();
        const ttl = GM_getValue('gm_cache_ttl', CONFIG.defaults.cacheTTL);
        let changed = false;
        for (const { key, entry } of findCacheCandidates(rawUrl, meta, targetLanguage, profile)) {
            let data;
            try { data = JSON.parse(GM_getValue(key, 'null')); } catch { data = null; }
            if ((ttl > 0 && Date.now() - entry.timestamp > ttl) || !data
                || data.version !== CONFIG.cacheVersion || data.profile !== profile || !Array.isArray(data.records)) {
                GM_deleteValue(key);
                delete meta[key];
                changed = true;
                continue;
            }
            for (const record of data.records) {
                if (record && typeof record.key === 'string'
                    && (record.translation === null || typeof record.translation === 'string')
                    && !records.has(record.key)) records.set(record.key, record);
            }
        }
        if (changed) saveCacheMeta(meta);
        return records;
    }

    function setCache(rawUrl, records, targetLanguage = getTargetLanguage(), profile = '') {
        const normalizedUrl = normalizeUrl(rawUrl);
        const language = normalizeTargetLanguage(targetLanguage);
        const key = generateCacheKey(normalizedUrl, language, profile);
        const data = JSON.stringify({ version: CONFIG.cacheVersion, profile, records: [...records.values()] });
        const dataSize = new Blob([data]).size;
        const maxSize = Number(GM_getValue('gm_max_cache_size', CONFIG.defaults.maxCacheSize));
        // An oversized page must not evict every other page or exceed the configured limit.
        if (!Number.isFinite(maxSize) || dataSize > maxSize || records.size === 0) return false;
        const meta = getCacheMeta();
        let totalSize = Object.entries(meta).reduce((sum, [entryKey, entry]) => sum + (entryKey === key ? 0 : entry.size), 0);
        const oldest = Object.keys(meta).filter(entryKey => entryKey !== key)
            .sort((a, b) => meta[a].timestamp - meta[b].timestamp);
        while (totalSize + dataSize > maxSize && oldest.length) {
            const oldKey = oldest.shift();
            totalSize -= meta[oldKey].size;
            GM_deleteValue(oldKey);
            delete meta[oldKey];
        }
        GM_setValue(key, data);
        meta[key] = { version: CONFIG.cacheVersion, url: rawUrl, normalizedUrl, targetLanguage: language,
            profile, timestamp: Date.now(), size: dataSize };
        saveCacheMeta(meta);
        return true;
    }

    function applyCachedTranslations(entries, records) {
        const pending = [];
        const reused = new Map();
        let updated = 0;
        for (const entry of entries) {
            const record = records.get(entry.cacheKey);
            if (!record) { pending.push(entry); continue; }
            if (record.translation !== null) updated += applyPreparedTranslation({ id: entry.id, text: record.translation });
            reused.set(entry.cacheKey, record);
        }
        return { pending, reused, updated, hits: entries.length - pending.length };
    }

    function cacheCompletedBatch(batch, records) {
        for (const entry of batch.entries) {
            records.set(entry.cacheKey, { key: entry.cacheKey, translation: batch.translations.get(entry.id) ?? null });
        }
    }

    function clearCurrentPageCache() {
        const meta = getCacheMeta();
        // Clear all candidates that could contribute to this page/language, including other models.
        for (const { key } of findCacheCandidates(window.location.href, meta)) {
            GM_deleteValue(key);
            delete meta[key];
        }
        saveCacheMeta(meta);
    }

    function clearAllCache() {
        Object.keys(getCacheMeta()).forEach(key => GM_deleteValue(key));
        GM_deleteValue(CONFIG.cacheMetaKey);
    }

    function getCacheStats() {
        const entries = Object.values(getCacheMeta());
        return { totalSize: entries.reduce((sum, entry) => sum + entry.size, 0), count: entries.length };
    }
    
    // Format bytes to human-readable size
    function formatBytes(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    function escapeHtmlAttribute(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/"/g, '&quot;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    function escapeHtmlText(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    function buildTranslationPrompt(targetLanguage = getTargetLanguage()) {
        const language = normalizeTargetLanguage(targetLanguage);
        return `You are a CSV translator.
Input format: id,"text content"
Task: Translate "text content" to ${language}.

Rules:
1. Keep each "id" exactly as-is.
2. Output a CSV line only for nodes that actually need translation. If a node remains unchanged, do not output that node.
3. Escape any double quotes in translated_text by doubling them (e.g., " becomes "").
4. If the text is already in ${language}, keep it unchanged and omit that node from output.
5. Do not translate proper nouns (names, brands, places, organizations, etc.). Keep them in the original language. If the whole node remains unchanged, omit it.
6. For specialist or technical terms without a widely accepted translation in ${language}, keep them in the original language. If the whole node remains unchanged, omit it.
7. Preserve all HTML markup, placeholders, variables, and format tokens exactly as they appear.
8. Keep sentence structure close to the original. Do not merge, split, omit, or reorder records.
9. Never output Markdown fences, CSV headers, explanations, or blank lines.
10. Never include newline characters inside translated_text.
11. After all translated nodes have been output, output exactly one final line: ${CONFIG.finishNodeId},"finish".

Provide only the translated CSV lines plus the final finish line.
`;
    }

    function createTranslationBatches(entries, batchNodeCount) {
        const batches = [];
        for (let i = 0; i < entries.length; i += batchNodeCount) {
            const slice = entries.slice(i, i + batchNodeCount);
            batches.push({
                index: batches.length,
                entries: slice,
                entryIndexes: new Map(slice.map((entry, entryIndex) => [entry.id, entryIndex])),
                csvInput: buildCsvFromEntries(slice),
                resultLines: [],
                pendingCsvBuffer: '',
                appliedIds: new Set(),
                translations: new Map(),
                attempts: 0,
                nextEntryIndex: 0,
                finishReceived: false,
                completed: false
            });
        }
        return batches;
    }

    function createProgressState(totalNodes, totalBatches, workerCount) {
        return {
            totalNodes,
            updatedNodes: 0,
            skippedNodes: 0,
            completedNodes: 0,
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

        const completedNodes = progress.updatedNodes + progress.skippedNodes;
        const percent = progress.totalNodes === 0
            ? 0
            : Math.floor((completedNodes / progress.totalNodes) * 100);
        const retryText = progress.retriedBatches > 0 ? ` | 重试 ${progress.retriedBatches}` : '';
        const failureText = progress.failedBatches > 0 ? ` | 失败 ${progress.failedBatches}` : '';

        return `翻译中 ${completedNodes}/${progress.totalNodes} (${percent}%) | 已更新 ${progress.updatedNodes} | 已跳过 ${progress.skippedNodes} | 批次 ${progress.finishedBatches}/${progress.totalBatches} | 活动 ${progress.runningBatches}/${progress.workerCount}${retryText}${failureText}`;
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

    function isIsolatedRuntime() {
        return typeof GM_info !== 'undefined' && ['dom', 'js'].includes(GM_info.sandboxMode);
    }

    function requireIsolatedRuntime() {
        if (isIsolatedRuntime()) return true;
        showToast('需要油猴隔离沙箱 (dom/js)。当前运行模式不支持安全读取或使用密钥，请检查脚本管理器设置。', 'error');
        return false;
    }

    function updateKeyStatus() {
        if (!state.isUIInitialized) return;
        const platform = document.getElementById('gm-platform').value;
        document.getElementById('gm-key-status').textContent = !isIsolatedRuntime() ? '隔离沙箱不可用'
            : GM_getValue(`gm_api_key_${platform}`, '') ? '已配置' : '未配置';
    }

    function configureApiKey(platform) {
        if (!requireIsolatedRuntime() || !Object.hasOwn(CONFIG.platforms, platform)) return;
        const name = CONFIG.platforms[platform].name;
        // Browser dialogs belong to the isolated world. Never prefill or place a key in page DOM.
        const value = window.prompt(`设置 ${name} API 密钥。取消保留原值；留空后确认可删除密钥。`, '');
        if (value === null) return;
        const key = value.trim();
        if (key) GM_setValue(`gm_api_key_${platform}`, key);
        else {
            if (!window.confirm(`删除 ${name} 的已保存密钥？`)) return;
            GM_deleteValue(`gm_api_key_${platform}`);
        }
        updateKeyStatus();
        showToast(`${name} 密钥已${key ? '保存' : '删除'}`, 'success');
    }

    function configureOpenAIBaseUrl() {
        if (!requireIsolatedRuntime()) return;
        const value = window.prompt('设置 OpenAI / 兼容 API Base URL。留空恢复官方地址。', GM_getValue('gm_openai_base_url', CONFIG.platforms.openai.endpoint));
        if (value === null) return;
        let url;
        try { url = normalizeOpenAIBaseUrl(value); } catch (error) { showToast(error.message, 'error'); return; }
        const warning = url.startsWith('http:') ? '\n注意：HTTP 不加密。' : '';
        if (!window.confirm(`翻译内容与 OpenAI 密钥将发送到：\n${url}${warning}\n确认使用此地址？`)) return;
        GM_setValue('gm_openai_base_url', url);
        if (state.isUIInitialized) document.getElementById('gm-openai-base-url').textContent = url;
        showToast('API Base URL 已保存，下次翻译生效', 'success');
    }

    function init() {
        GM_registerMenuCommand('🚀 翻译网页 (Alt+T)', () => startTranslationProcess());
        GM_registerMenuCommand('🔄 重试未完成的翻译', retryFailedTranslation);
        GM_registerMenuCommand('⚙️ 设置', openSettings);
        for (const [platform, config] of Object.entries(CONFIG.platforms)) {
            GM_registerMenuCommand(`🔑 ${config.name} 密钥`, () => configureApiKey(platform));
        }
        GM_registerMenuCommand('🔗 OpenAI / 兼容 API 地址', configureOpenAIBaseUrl);
        document.addEventListener('keydown', event => {
            if (!event.isTrusted) return;
            if (event.altKey && event.key.toLowerCase() === 't') { event.preventDefault(); void startTranslationProcess(); }
            if (event.altKey && event.key.toLowerCase() === 's') { event.preventDefault(); openSettings(); }
        });
        console.log('[Gemini Translator] Ready.');
    }

    // ================= Text Node Extraction =================

    function extractTextNodes() {
        const eligibility = new WeakMap();
        const styles = new WeakMap();
        function styleFor(element) {
            if (!styles.has(element)) styles.set(element, window.getComputedStyle(element));
            return styles.get(element);
        }
        const excluded = 'script,style,noscript,textarea,code,pre,svg,path,kbd,var,select,option,'
            + '[translate="no"],.notranslate,[contenteditable]:not([contenteditable="false"]),'
            + '[hidden],[aria-hidden="true"],gm-translator,qr-scanner-ui,#gm-translator-container';
        function eligible(element) {
            if (!element) return true;
            if (eligibility.has(element)) return eligibility.get(element);
            const style = styleFor(element);
            const collapsedTablePart = style.visibility === 'collapse' && /^table-(row|column)(-group)?$/.test(style.display);
            const allowed = !element.matches(excluded) && !element.isContentEditable
                && style.display !== 'none' && !collapsedTablePart
                && style.opacity !== '0' && eligible(element.parentElement);
            eligibility.set(element, allowed);
            return allowed;
        }
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
            // visibility can be overridden by a visible descendant; display/opacity cannot.
            acceptNode: node => node.parentElement && /[^\s\d]/.test(node.nodeValue) && eligible(node.parentElement)
                && styleFor(node.parentElement).visibility === 'visible' ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT
        });
        const nodes = [];
        let node;
        while ((node = walker.nextNode())) nodes.push(node);
        return nodes;
    }

    // ================= CSV Processing =================

    function generateTranslationEntries(nodes) {
        state.textNodeMap.clear();
        state.sourceTexts.clear();
        const tagCounters = {};
        const contexts = new WeakMap();
        return nodes.map((node, index) => {
            const parent = node.parentElement;
            const tag = parent.tagName.toUpperCase();
            if (!tagCounters[tag]) tagCounters[tag] = 0;
            const id = `${tag}_${tagCounters[tag]++}_${index}`;
            const sourceText = node.nodeValue;
            if (!contexts.has(parent)) contexts.set(parent, parent.textContent);
            state.textNodeMap.set(id, node);
            state.sourceTexts.set(node, sourceText);
            return { id, node, sourceText,
                cacheKey: JSON.stringify([tag, sourceText, contexts.get(parent)]),
                text: sourceText.replace(/"/g, '""').replace(/[\r\n]+/g, ' ') };
        });
    }

    function buildCsvFromEntries(entries) {
        return entries.map(entry => `${entry.id},"${entry.text}"`).join('\n');
    }

    function parseCsvTranslationLine(rawLine) {
        const line = rawLine.trim();
        if (!line) return null;
        const match = line.match(/^([^,\s"]+),"((?:[^"]|"")*)"$/);
        if (!match) throw new Error('翻译结果包含无效 CSV 记录');
        const text = match[2].replace(/""/g, '"');
        const type = match[1] === CONFIG.finishNodeId ? 'finish' : 'translation';
        if (type === 'finish' && text !== 'finish') throw new Error('翻译结束标记无效');
        return { type, line, id: match[1], text };
    }

    function applyPreparedTranslation(preparedLine) {
        const node = state.textNodeMap.get(preparedLine.id);
        if (!node?.isConnected || !state.sourceTexts.has(node)
            || node.nodeValue !== state.sourceTexts.get(node) || node.nodeValue === preparedLine.text) return 0;
        state.originalTexts.set(node, node.nodeValue);
        state.lastAppliedTexts.set(node, preparedLine.text);
        node.nodeValue = preparedLine.text;
        state.isTranslated = true;
        return 1;
    }


    function getBatchSkippedCount(batch) {
        return Math.max(0, batch.nextEntryIndex - batch.appliedIds.size);
    }

    function flushBatchCsvBuffer(batch, force = false, runId = state.activeTranslationRunId) {
        if (!isActiveTranslationRun(runId)) return { appliedCount: 0, skippedCount: 0 };
        const segments = batch.pendingCsvBuffer.replace(/\r/g, '').split('\n');
        const lines = (force ? segments : segments.slice(0, -1)).map(parseCsvTranslationLine).filter(Boolean);
        batch.pendingCsvBuffer = force ? '' : (segments[segments.length - 1] || '');
        // Validate the whole chunk before applying any part, so counters and rollback stay paired.
        let nextIndex = batch.nextEntryIndex;
        let finished = batch.finishReceived;
        for (const line of lines) {
            if (finished) throw new Error('结束标记之后仍有翻译记录');
            if (line.type === 'finish') { finished = true; continue; }
            const index = batch.entryIndexes.get(line.id);
            if (index === undefined) throw new Error(`翻译结果包含不属于本批的 ID: ${line.id}`);
            if (index < nextIndex) throw new Error(`翻译记录重复或乱序: ${line.id}`);
            nextIndex = index + 1;
        }
        let appliedCount = 0;
        const previousSkipped = getBatchSkippedCount(batch);
        for (const line of lines) {
            batch.resultLines.push(line.line);
            if (line.type === 'finish') {
                batch.finishReceived = true;
                batch.nextEntryIndex = batch.entries.length;
            } else {
                batch.nextEntryIndex = batch.entryIndexes.get(line.id) + 1;
                batch.translations.set(line.id, line.text);
                if (applyPreparedTranslation(line)) {
                    batch.appliedIds.add(line.id);
                    appliedCount++;
                }
            }
        }
        return { appliedCount, skippedCount: getBatchSkippedCount(batch) - previousSkipped };
    }

    function appendBatchTranslationChunk(batch, chunkText, runId = state.activeTranslationRunId) {
        if (!isActiveTranslationRun(runId)) return { appliedCount: 0, skippedCount: 0 };
        if (!chunkText) return { appliedCount: 0, skippedCount: 0 };
        batch.pendingCsvBuffer += chunkText;
        return flushBatchCsvBuffer(batch, false, runId);
    }

    function restoreNode(node) {
        let restored = 0;
        if (node?.isConnected && state.originalTexts.has(node)
            && node.nodeValue === state.lastAppliedTexts.get(node)) {
            node.nodeValue = state.originalTexts.get(node);
            restored = 1;
        }
        state.originalTexts.delete(node);
        state.lastAppliedTexts.delete(node);
        return restored;
    }

    function restoreOriginalText() {
        let count = 0;
        for (const node of state.originalTexts.keys()) count += restoreNode(node);
        state.textNodeMap.clear();
        state.sourceTexts.clear();
        state.isTranslated = false;
        return count;
    }

    function isActiveTranslationRun(runId) {
        return state.isTranslating && state.activeTranslationRunId === runId;
    }

    function cancelActiveTranslationAndRestoreOriginal() {
        state.activeTranslationRunId++;

        state.activeRequests.forEach(request => {
            try {
                if (request && typeof request.abort === 'function') request.abort();
            } catch (error) {
                console.warn('[Gemini Translator] Failed to abort request:', error);
            }
        });
        state.activeRequests.clear();
        for (const cancel of state.retryWaits) cancel();

        const count = restoreOriginalText();
        state.isTranslating = false;
        state.translationProgress = null;
        state.lastProgressUpdateAt = 0;

        if (state.currentToastId) {
            state.currentToastId = updateToast(state.currentToastId, `已取消翻译并还原原始状态 (${count} 个节点)`, 'success');
        } else {
            showToast(`已取消翻译并还原原始状态 (${count} 个节点)`, 'success');
        }
    }

    // ================= Translation Process =================

    async function retryFailedTranslation() {
        if (state.isTranslating) return;
        if (!state.retryContext) { showToast('没有待重试的翻译', 'info'); return; }
        restoreOriginalText();
        await startTranslationProcess({ resume: true });
    }

    async function startTranslationProcess({ resume = false } = {}) {
        ensureUI();
        if (state.isTranslating) { cancelActiveTranslationAndRestoreOriginal(); return; }
        if (state.isTranslated) {
            showToast(`已还原原始状态 (${restoreOriginalText()} 个节点)`, 'success');
            return;
        }
        if (!requireIsolatedRuntime()) return;
        const platform = GM_getValue('gm_platform', CONFIG.defaults.platform);
        if (!Object.hasOwn(CONFIG.platforms, platform)) { showToast('平台配置无效', 'error'); return; }
        const previousRun = resume ? state.retryContext : null;
        state.isTranslating = true;
        const runId = ++state.activeTranslationRunId;
        state.originalTexts.clear();
        state.lastAppliedTexts.clear();
        state.translationProgress = null;
        state.lastProgressUpdateAt = 0;
        state.currentToastId = showToast('正在分析页面结构...', 'loading', 0);
        try {
            const currentUrl = window.location.href;
            const cacheEnabled = GM_getValue('gm_cache_enable', true);
            const targetLanguage = getTargetLanguage();
            const modelSettings = getPlatformModelSettings(platform);
            const model = modelSettings.customModel.trim() || modelSettings.model || CONFIG.platforms[platform].models[0];
            const prompt = buildTranslationPrompt(targetLanguage);
            const requestSettings = getRequestSettings(platform, model);
            const profile = JSON.stringify([platform, model, prompt, requestSettings.protocol,
                requestSettings.baseUrl, requestSettings.reasoningEffort]);
            const entries = generateTranslationEntries(extractTextNodes());
            if (!entries.length) throw new Error('未找到可翻译的文本');
            const cached = cacheEnabled ? getCache(currentUrl, targetLanguage, profile) : new Map();
            if (previousRun?.url === currentUrl && previousRun.profile === profile) {
                for (const [key, record] of previousRun.records) cached.set(key, record);
            }
            const { pending, reused, updated, hits } = applyCachedTranslations(entries, cached);
            state.retryContext = { url: currentUrl, profile, records: reused };
            let cacheWarning = '';
            const retainBatch = batch => {
                cacheCompletedBatch(batch, reused);
                if (cacheEnabled) {
                    try {
                        if (!setCache(currentUrl, reused, targetLanguage, profile)) cacheWarning = '；结果超出缓存限制，未写入本地缓存';
                    } catch { cacheWarning = '；本地缓存保存失败'; }
                }
            };
            if (!pending.length) {
                state.retryContext = null;
                state.currentToastId = updateToast(state.currentToastId, `⚡ 缓存已验证 (${hits} 个节点，更新 ${updated} 个)`, 'success');
                return;
            }
            const apiKey = GM_getValue(`gm_api_key_${platform}`, '');
            if (!apiKey) throw new Error(`请通过油猴菜单配置 ${CONFIG.platforms[platform].name} 密钥`);
            const batchNodeCount = getClampedIntegerSetting('gm_batch_node_count', CONFIG.defaults.batchNodeCount, 1, 5000);
            const concurrency = getClampedIntegerSetting('gm_concurrency', CONFIG.defaults.concurrency, 1, 32);
            const batches = createTranslationBatches(pending, batchNodeCount);
            state.translationProgress = createProgressState(entries.length, batches.length, Math.min(concurrency, batches.length));
            state.translationProgress.updatedNodes = updated;
            state.translationProgress.skippedNodes = hits - updated;
            state.translationProgress.completedNodes = hits;
            refreshProgressToast(true);
            await translateAllBatches({ runId, platform, apiKey, model, prompt, requestSettings, batches,
                isDebug: GM_getValue('gm_debug', false), onBatchComplete: retainBatch });
            if (!isActiveTranslationRun(runId)) return;
            state.retryContext = null;
            state.currentToastId = updateToast(state.currentToastId,
                `✅ 翻译完成 (完成 ${state.translationProgress.completedNodes}/${entries.length} 个节点，更新 ${state.translationProgress.updatedNodes} 个，跳过 ${state.translationProgress.skippedNodes} 个)${cacheWarning}`, 'success');
        } catch (error) {
            if (!isActiveTranslationRun(runId)) return;
            console.error('[Gemini Translator]', error.message);
            state.currentToastId = updateToast(state.currentToastId,
                `Error: ${error.message}；可用油猴菜单重试未完成部分，Alt+T 还原已应用译文`, 'error');
        } finally {
            if (state.activeTranslationRunId === runId) {
                state.isTranslated = state.originalTexts.size > 0;
                state.isTranslating = false;
                state.translationProgress = null;
                state.lastProgressUpdateAt = 0;
            }
        }
    }

    function waitForRetry(runId, milliseconds) {
        if (!isActiveTranslationRun(runId)) return Promise.resolve(false);
        if (milliseconds <= 0) return Promise.resolve(true);
        return new Promise(resolve => {
            const until = Date.now() + milliseconds;
            let timer;
            const finish = value => { clearTimeout(timer); state.retryWaits.delete(cancel); resolve(value); };
            const cancel = () => finish(false);
            const tick = () => {
                if (!isActiveTranslationRun(runId)) { finish(false); return; }
                const remaining = until - Date.now();
                if (remaining <= 0) finish(true);
                else timer = setTimeout(tick, Math.min(remaining, 2147483647));
            };
            state.retryWaits.add(cancel);
            tick();
        });
    }

    function getRetryDelay(error, attempts) {
        return Math.max(error.retryAfterMs || 0, Math.min(30000, 500 * 2 ** (attempts - 1)) + Math.random() * 250);
    }

    async function translateAllBatches({ runId, platform, apiKey, model, prompt, batches, isDebug,
        requestSettings = getRequestSettings(platform, model), onBatchComplete = () => {} }) {
        const errors = [];
        const pending = [...batches];
        let fatalError = null;
        let cooldownUntil = 0;
        async function worker() {
            while (isActiveTranslationRun(runId) && !fatalError) {
                // A concurrent rate-limit response can extend the deadline during this wait.
                while (cooldownUntil > Date.now()) {
                    if (!await waitForRetry(runId, cooldownUntil - Date.now()) || fatalError) return;
                }
                if (!isActiveTranslationRun(runId) || fatalError) return;
                const batch = pending.shift();
                if (!batch) return;
                state.translationProgress.runningBatches++;
                refreshProgressToast(true);
                let retryDelay = null;
                try {
                    batch.attempts++;
                    await translateSingleBatch({ runId, batch, platform, apiKey, model, prompt, requestSettings, isDebug });
                    if (!isActiveTranslationRun(runId)) return;
                    onBatchComplete(batch);
                } catch (error) {
                    if (!isActiveTranslationRun(runId) || !state.translationProgress) return;
                    state.translationProgress.skippedNodes -= getBatchSkippedCount(batch);
                    state.translationProgress.updatedNodes -= resetBatchForRetry(batch);
                    if (error.fatal) {
                        fatalError = error;
                        pending.length = 0;
                        for (const request of state.activeRequests) request.abort();
                        for (const cancel of state.retryWaits) cancel();
                    }
                    if (!fatalError && error.retryable !== false && batch.attempts <= CONFIG.defaults.maxBatchRetries) {
                        retryDelay = getRetryDelay(error, batch.attempts);
                        if (error.status === 429 || error.retryAfterMs > 0) cooldownUntil = Math.max(cooldownUntil, Date.now() + retryDelay);
                        state.translationProgress.retriedBatches++;
                    } else {
                        errors.push({ batch, error });
                        state.translationProgress.failedBatches++;
                    }
                    if (isDebug) console.warn(`[Gemini Translator] Batch ${batch.index + 1}:`, error.message);
                } finally {
                    if (isActiveTranslationRun(runId) && state.translationProgress) {
                        state.translationProgress.runningBatches--;
                        refreshProgressToast(true);
                    }
                }
                if (retryDelay !== null && await waitForRetry(runId, retryDelay) && !fatalError) pending.push(batch);
            }
        }
        await Promise.all(Array.from({ length: state.translationProgress.workerCount }, () => worker()));
        if (!isActiveTranslationRun(runId)) return '';
        if (fatalError) throw fatalError;
        if (errors.length) {
            const { batch, error } = errors[0];
            throw new Error(`第 ${batch.index + 1} 批在 ${batch.attempts} 次尝试后失败: ${error.message}`);
        }
        return batches.map(batch => batch.resultLines.join('\n')).filter(Boolean).join('\n');
    }

    function resetBatchForRetry(batch) {
        const revertedCount = batch.appliedIds.size;
        for (const id of batch.appliedIds) restoreNode(state.textNodeMap.get(id));
        batch.pendingCsvBuffer = '';
        batch.resultLines = [];
        batch.appliedIds.clear();
        batch.translations.clear();
        batch.nextEntryIndex = 0;
        batch.finishReceived = false;
        batch.completed = false;
        return revertedCount;
    }

    function normalizeTranslationError(value) {
        let message = '翻译请求失败';
        try { message = String(value?.message ?? value ?? message); } catch { /* Arbitrary stream error reasons are allowed. */ }
        const error = new Error(message);
        for (const key of ['retryable', 'fatal', 'status', 'retryAfterMs']) {
            try {
                const field = value?.[key];
                if ((['retryable', 'fatal'].includes(key) && typeof field === 'boolean')
                    || (['status', 'retryAfterMs'].includes(key) && Number.isFinite(field))) error[key] = field;
            } catch { /* Error normalization must always settle the request. */ }
        }
        return error;
    }

    function translateSingleBatch({ runId, batch, platform, apiKey, model, prompt, isDebug, requestSettings }) {
        return new Promise((resolve, reject) => {
            let requestOptions;
            try { requestOptions = createStreamRequestOptions(platform, apiKey, model, prompt, batch.csvInput, requestSettings); }
            catch (error) { reject(Object.assign(normalizeTranslationError(error), { retryable: false, fatal: true })); return; }
            const parser = { buffer: '' };
            const decoder = new TextDecoder('utf-8', { fatal: true });
            let processedLength = 0;
            let settled = false;
            let readable = false;
            let request;
            let reader;
            let abortTransport = false;
            const active = () => !settled && isActiveTranslationRun(runId);
            const controller = { abort: () => finish(Object.assign(new Error('翻译请求已取消'), { retryable: false })) };
            function finish(error, failed = true) {
                if (settled) return;
                settled = true;
                state.activeRequests.delete(controller);
                if (failed) {
                    abortTransport = true;
                    try { request?.abort?.(); } catch { /* Settlement must survive transport errors. */ }
                    try { Promise.resolve(reader?.cancel?.()).catch(() => {}); } catch { /* Best effort. */ }
                    const safeError = normalizeTranslationError(error);
                    if (apiKey) safeError.message = safeError.message.split(apiKey).join('[redacted]');
                    reject(safeError);
                } else resolve();
            }
            function recordProgress({ appliedCount, skippedCount }) {
                if (!active() || !state.translationProgress) return;
                state.translationProgress.updatedNodes += appliedCount;
                state.translationProgress.skippedNodes += skippedCount;
                if (appliedCount || skippedCount) refreshProgressToast();
            }
            function payload(data) {
                if (active()) recordProgress(appendBatchTranslationChunk(batch, extractStreamTextFromPayload(data, platform), runId));
            }
            function complete() {
                if (!active()) { controller.abort(); return; }
                flushSSEBuffer(parser, payload);
                recordProgress(flushBatchCsvBuffer(batch, true, runId));
                if (!batch.finishReceived) throw new Error('流式响应未输出 finish 结点');
                batch.completed = true;
                state.translationProgress.completedNodes += batch.entries.length;
                state.translationProgress.finishedBatches++;
                if (isDebug) console.log(`[Gemini Translator] Batch ${batch.index + 1} completed.`);
                finish(null, false);
            }
            function text(response) {
                if (!active() || typeof response !== 'string' || !response) return;
                if (response.length < processedLength) throw new Error('流式响应累计文本发生回退');
                const delta = response.slice(processedLength);
                processedLength = response.length;
                if (delta) consumeSSEPayload(delta, parser, payload);
            }
            const guarded = callback => response => {
                if (settled) return;
                if (!isActiveTranslationRun(runId)) { controller.abort(); return; }
                try { callback(response); } catch (error) { finish(error); }
            };
            async function readStream(stream) {
                try {
                    reader = typeof stream.getReader === 'function' ? stream.getReader() : stream;
                    while (active()) {
                        const result = await reader.read();
                        if (!active()) return;
                        if (!result || result.done) break;
                        const chunk = typeof result.value === 'string' ? result.value : decoder.decode(result.value, { stream: true });
                        consumeSSEPayload(chunk, parser, payload);
                    }
                    if (!active()) return;
                    consumeSSEPayload(decoder.decode(), parser, payload);
                    complete();
                } catch (error) { finish(error); }
                finally { try { reader?.releaseLock?.(); } catch { /* A cancelled reader may already be released. */ } }
            }
            if (!isActiveTranslationRun(runId)) { resolve(); return; }
            state.activeRequests.add(controller);
            try {
                request = GM_xmlhttpRequest({
                    ...requestOptions,
                    onloadstart: guarded(res => {
                        if (isHttpErrorStatus(res.status)) throw getHttpError(res);
                        const stream = res.response;
                        if (!readable && (typeof stream?.getReader === 'function' || typeof stream?.read === 'function')) {
                            if (processedLength) throw new Error('响应已开始使用文本传输，不能切换到 readable');
                            readable = true;
                            void readStream(stream);
                        }
                    }),
                    onprogress: guarded(res => {
                        if (isHttpErrorStatus(res.status)) throw getHttpError(res);
                        if (!readable) text(res.responseText || res.response);
                    }),
                    onload: guarded(res => {
                        if (isHttpErrorStatus(res.status)) throw getHttpError(res);
                        // Once claimed, the reader exclusively owns EOF, decoding and finalization.
                        if (readable) return;
                        text(res.responseText || res.response);
                        complete();
                    }),
                    onabort: () => controller.abort(),
                    ontimeout: () => finish(new Error('翻译请求超时')),
                    onerror: error => finish(new Error(getNetworkErrorMessage(error)))
                });
                // Some transports invoke a terminal callback before returning their handle.
                if (abortTransport) { try { request?.abort?.(); } catch { /* Already settled. */ } }
            } catch (error) { finish(Object.assign(normalizeTranslationError(error), { retryable: false, fatal: true })); }
        });
    }

    function getRequestSettings(platform, model) {
        const protocol = platform === 'openai' ? getOpenAIProtocol() : 'chat';
        return {
            protocol,
            baseUrl: platform === 'openai' ? normalizeOpenAIBaseUrl(GM_getValue('gm_openai_base_url', '')) : CONFIG.platforms[platform].endpoint,
            reasoningEffort: getReasoningEffort(platform, model),
            serviceTier: platform === 'openai' ? getOpenAIServiceTier(protocol) : ''
        };
    }

    function getHttpError(res) {
        const status = res.status;
        const retryable = status === 408 || status === 409 || status === 429 || status >= 500;
        const value = String(res.responseHeaders || '').match(/^retry-after:\s*(.*?)\s*$/im)?.[1];
        let retryAfterMs = 0;
        if (value) {
            retryAfterMs = /^\d+(?:\.\d+)?$/.test(value) ? Number(value) * 1000 : Date.parse(value) - Date.now();
            if (!Number.isFinite(retryAfterMs) || retryAfterMs < 0) retryAfterMs = 0;
        }
        return Object.assign(new Error(getResponseErrorMessage(res)), { status, retryable, fatal: !retryable, retryAfterMs });
    }

    function createStreamRequestOptions(platform, apiKey, model, prompt, csvInput, settings = getRequestSettings(platform, model)) {
        const { reasoningEffort, protocol, baseUrl, serviceTier } = settings;
        if (platform === 'gemini') {
            const body = {
                system_instruction: { parts: [{ text: prompt }] },
                contents: [{ parts: [{ text: csvInput }] }]
            };
            if (reasoningEffort) {
                body.generationConfig = { thinkingConfig: { thinkingLevel: reasoningEffort } };
            }
            return {
                method: 'POST',
                url: `${CONFIG.platforms.gemini.endpoint}/${model}:streamGenerateContent?alt=sse`,
                responseType: 'stream',
                headers: {
                    'Content-Type': 'application/json',
                    'x-goog-api-key': apiKey
                },
                data: JSON.stringify(body)
            };
        }

        const body = protocol === 'responses'
            ? { model, instructions: prompt, input: csvInput, stream: true, store: false }
            : {
                model,
                messages: [
                    { role: 'system', content: prompt },
                    { role: 'user', content: csvInput }
                ],
                stream: true
            };
        let url;
        if (platform === 'openai') {
            url = `${baseUrl}/${protocol === 'responses' ? 'responses' : 'chat/completions'}`;
            if (serviceTier) body.service_tier = serviceTier;
            body.store = false;
            if (reasoningEffort) {
                if (protocol === 'responses') body.reasoning = { effort: reasoningEffort };
                else body.reasoning_effort = reasoningEffort;
            }
        } else {
            url = CONFIG.platforms.openrouter.endpoint;
            if (reasoningEffort) {
                body.reasoning = reasoningEffort === 'none'
                    ? { enabled: false }
                    : { effort: reasoningEffort };
            }
        }

        return {
            method: 'POST',
            url,
            responseType: 'stream',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${apiKey}`
            },
            data: JSON.stringify(body)
        };
    }

    function consumeSSEPayload(chunk, parserState, onPayload) {
        // Keep line endings intact until a complete event arrives, including
        // when the transport splits a CRLF pair across two chunks.
        parserState.buffer += chunk;
        const events = parserState.buffer.split(/\r\n\r\n|\n\n|\r\r/);
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
            .split(/\r\n|\n|\r/)
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
                .filter(part => !part.thought)
                .map(part => part.text || '')
                .join('');
        }

        if (platform === 'openai') {
            if (data.type === 'error') {
                throw new Error(data.message || data.code || 'OpenAI 流式响应错误');
            }
            if (data.type === 'response.failed' || data.type === 'response.incomplete') {
                throw new Error(data.response?.error?.message
                    || `OpenAI 响应未完成: ${data.response?.incomplete_details?.reason || data.type}`);
            }
            if (data.type === 'response.refusal.delta' || data.type === 'response.refusal.done') {
                throw new Error(`OpenAI 拒绝翻译: ${data.delta || data.refusal || ''}`);
            }
            if (data.type === 'response.output_text.delta') {
                return typeof data.delta === 'string' ? data.delta : '';
            }
            // Completed events contain the full output again. Only deltas
            // belong in the CSV buffer; ignore lifecycle and reasoning events.
            if (data.type?.startsWith('response.')) return '';
        }

        const hasErrorFinishReason = (data.choices || []).some(choice => choice.finish_reason === 'error');
        if (hasErrorFinishReason) {
            throw new Error(`${CONFIG.platforms[platform].name} 流式响应中断`);
        }
        if (platform === 'openai') {
            const interrupted = (data.choices || []).find(choice => ['length', 'content_filter'].includes(choice.finish_reason));
            if (interrupted) throw new Error(`OpenAI 响应未完成: ${interrupted.finish_reason}`);
            const refusal = (data.choices || []).find(choice => choice.delta?.refusal);
            if (refusal) throw new Error(`OpenAI 拒绝翻译: ${refusal.delta.refusal}`);
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
                        <h3 style="color:#fff; margin:0; font-size:18px;">Gemini 翻译器 V7.4.0</h3>
                    </div>
                    
                    <div>
                        <label class="gm-label">平台</label>
                        <select id="gm-platform" class="gm-select">
                            <option value="gemini">Google Gemini</option>
                            <option value="openrouter">OpenRouter</option>
                            <option value="openai">OpenAI / 兼容 API</option>
                        </select>
                    </div>

                    <div>
                        <label class="gm-label">目标翻译语言</label>
                        <select id="gm-target-language-select" class="gm-select">
                            ${CONFIG.commonTargetLanguages.map(language => `<option value="${escapeHtmlAttribute(language)}">${escapeHtmlText(language)}</option>`).join('')}
                            <option value="__custom__">自定义...</option>
                        </select>
                        <input type="text" id="gm-target-language-custom" class="gm-input gm-hidden" style="margin-top:8px" placeholder="输入目标语言，例如 Polish" spellcheck="false">
                    </div>
                    
                    <div id="gm-api-key-container">
                        <label class="gm-label" id="gm-key-label">API 密钥</label>
                        <div id="gm-key-status"></div>
                        <div style="color:#9aa0a6; font-size:12px; margin-top:6px">通过油猴菜单管理各平台密钥，密钥不会显示在网页中。</div>
                    </div>

                    <div id="gm-openai-settings" class="gm-hidden">
                        <label class="gm-label">API Base URL</label>
                        <div id="gm-openai-base-url" style="overflow-wrap:anywhere"></div>
                        <div style="color:#9aa0a6; font-size:12px; margin-top:6px">通过油猴菜单“OpenAI / 兼容 API 地址”修改。</div>
                        <label class="gm-label" style="margin-top:12px">API 协议</label>
                        <select id="gm-openai-protocol" class="gm-select">
                            <option value="chat">Chat Completions (/chat/completions)</option>
                            <option value="responses">Responses (/responses)</option>
                        </select>
                        <label class="gm-label" style="margin-top:12px">Service Tier（服务等级）</label>
                        <select id="gm-openai-service-tier" class="gm-select">
                            <option value="">不发送（兼容接口）</option>
                            <option value="auto">Auto（跟随项目设置）</option>
                            <option value="default">Default（标准）</option>
                            <option value="flex">Flex</option>
                            <option value="priority">Priority</option>
                            <option value="fast">Fast</option>
                            <option value="scale">Scale</option>
                            <option value="ultrafast" id="gm-openai-ultrafast">Ultrafast（仅 Responses）</option>
                        </select>
                    </div>
                    
                    <div>
                        <label class="gm-label">模型</label>
                        <select id="gm-model" class="gm-select" style="margin-bottom:8px"></select>
                        <input type="text" id="gm-model-input" class="gm-input" placeholder="手动模型代码（优先于列表）">
                    </div>

                    <div>
                        <label class="gm-label">Reasoning / Thinking 强度</label>
                        <select id="gm-reasoning-effort" class="gm-select"></select>
                        <div id="gm-reasoning-hint" style="color:#9aa0a6; font-size:12px; margin-top:6px"></div>
                    </div>

                    <div class="gm-checkbox-group">
                        <label class="gm-checkbox-label">
                            <input type="checkbox" id="gm-cache"> 启用本地缓存
                        </label>
                        <label class="gm-checkbox-label">
                            <input type="checkbox" id="gm-debug"> 调试模式
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
                        (<span id="gm-cache-count">0</span> 个缓存)
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
        document.getElementById('gm-openai-protocol').onchange = updateOpenAIProtocolUI;
        document.getElementById('gm-model').onchange = () => updateReasoningUI();
        document.getElementById('gm-model-input').oninput = () => updateReasoningUI();
        document.getElementById('gm-target-language-select').onchange = updateTargetLanguageUI;
        
        document.getElementById('gm-save').onclick = event => {
            if (!event.isTrusted) return;
            const platform = document.getElementById('gm-platform').value;
            if (!Object.hasOwn(CONFIG.platforms, platform)) return;

            capturePlatformSettings();
            state.settingsDrafts.forEach((draft, draftPlatform) => {
                GM_setValue(`gm_model_${draftPlatform}`, draft.model);
                GM_setValue(`gm_model_input_${draftPlatform}`, draft.customModel);
                GM_setValue(`gm_reasoning_effort_${draftPlatform}`, draft.reasoningEffort);
            });
            GM_setValue('gm_platform', platform);
            GM_setValue('gm_target_language', readTargetLanguageFromUI());
            GM_setValue('gm_openai_protocol', document.getElementById('gm-openai-protocol').value);
            GM_setValue('gm_openai_service_tier', document.getElementById('gm-openai-service-tier').value);
            GM_setValue('gm_cache_enable', document.getElementById('gm-cache').checked);
            GM_setValue('gm_debug', document.getElementById('gm-debug').checked);

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
            showToast('当前页面当前语言缓存已清除', 'success');
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
    function capturePlatformSettings() {
        if (!state.settingsPlatform) return;
        state.settingsDrafts.set(state.settingsPlatform, {
            model: document.getElementById('gm-model').value,
            customModel: document.getElementById('gm-model-input').value.trim(),
            reasoningEffort: document.getElementById('gm-reasoning-effort').value
        });
    }

    function updatePlatformUI() {
        capturePlatformSettings();
        const platform = document.getElementById('gm-platform').value;
        state.settingsPlatform = platform;
        const modelSelect = document.getElementById('gm-model');
        const draft = state.settingsDrafts.get(platform) || {
            ...getPlatformModelSettings(platform),
            reasoningEffort: getSavedReasoningEffort(platform)
        };
        updateKeyStatus();
        document.getElementById('gm-model-input').value = draft.customModel;
        
        // Update model dropdown
        modelSelect.innerHTML = '';
        // Keep a previously selected model visible even if it left the preset list.
        const models = [...CONFIG.platforms[platform].models];
        if (draft.model && !models.includes(draft.model)) models.push(draft.model);
        models.forEach(model => {
            const option = document.createElement('option');
            option.value = model;
            option.textContent = model;
            modelSelect.appendChild(option);
        });
        
        // Restore saved model if it exists in the list
        const savedModel = draft.model;
        if (models.includes(savedModel)) {
            modelSelect.value = savedModel;
        }

        document.getElementById('gm-openai-settings').classList.toggle('gm-hidden', platform !== 'openai');
        updateReasoningUI(draft.reasoningEffort);
    }

    function updateReasoningUI(savedEffort) {
        const platform = document.getElementById('gm-platform').value;
        const model = document.getElementById('gm-model-input').value.trim()
            || document.getElementById('gm-model').value;
        const select = document.getElementById('gm-reasoning-effort');
        const selectedEffort = savedEffort ?? select.value;
        const efforts = getReasoningEfforts(platform, model);
        select.innerHTML = '';
        ['', ...efforts].forEach(effort => {
            const option = document.createElement('option');
            option.value = effort;
            option.textContent = CONFIG.reasoningEffortLabels[effort];
            select.appendChild(option);
        });
        select.value = efforts.includes(selectedEffort) ? selectedEffort : '';
        select.disabled = efforts.length === 0;
        document.getElementById('gm-reasoning-hint').textContent = efforts.length === 0
            ? '此模型不支持按强度设置；使用模型默认行为。'
            : '默认保留服务端行为；自定义模型的可用档位以服务端为准。';
    }

    function updateOpenAIProtocolUI() {
        const isResponses = document.getElementById('gm-openai-protocol').value === 'responses';
        document.getElementById('gm-openai-ultrafast').disabled = !isResponses;
        const tierSelect = document.getElementById('gm-openai-service-tier');
        if (!isResponses && tierSelect.value === 'ultrafast') {
            tierSelect.value = CONFIG.defaults.openaiServiceTier;
        }
    }

    function updateTargetLanguageUI() {
        const select = document.getElementById('gm-target-language-select');
        const customInput = document.getElementById('gm-target-language-custom');
        if (!select || !customInput) return;

        if (select.value === '__custom__') {
            customInput.classList.remove('gm-hidden');
            customInput.focus();
        } else {
            customInput.classList.add('gm-hidden');
        }
    }

    function setTargetLanguageUI(language) {
        const normalizedLanguage = normalizeTargetLanguage(language);
        const select = document.getElementById('gm-target-language-select');
        const customInput = document.getElementById('gm-target-language-custom');
        if (!select || !customInput) return;

        if (CONFIG.commonTargetLanguages.includes(normalizedLanguage)) {
            select.value = normalizedLanguage;
            customInput.value = '';
        } else {
            select.value = '__custom__';
            customInput.value = normalizedLanguage;
        }

        updateTargetLanguageUI();
    }

    function readTargetLanguageFromUI() {
        const select = document.getElementById('gm-target-language-select');
        const customInput = document.getElementById('gm-target-language-custom');
        if (!select) return getTargetLanguage();

        if (select.value === '__custom__') {
            return normalizeTargetLanguage(customInput?.value);
        }

        return normalizeTargetLanguage(select.value);
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
        state.settingsPlatform = null;
        state.settingsDrafts.clear();
        const platform = GM_getValue('gm_platform', CONFIG.defaults.platform);
        document.getElementById('gm-platform').value = platform;
        document.getElementById('gm-openai-base-url').textContent = GM_getValue('gm_openai_base_url', CONFIG.platforms.openai.endpoint);
        const protocol = getOpenAIProtocol();
        document.getElementById('gm-openai-protocol').value = protocol;
        document.getElementById('gm-openai-service-tier').value = getOpenAIServiceTier(protocol);
        updateOpenAIProtocolUI();
        
        updatePlatformUI();
        
        setTargetLanguageUI(getTargetLanguage());
        document.getElementById('gm-cache').checked = GM_getValue('gm_cache_enable', true);
        document.getElementById('gm-debug').checked = GM_getValue('gm_debug', false);
        document.getElementById('gm-batch-size').value = getClampedIntegerSetting('gm_batch_node_count', CONFIG.defaults.batchNodeCount, 1, 5000);
        document.getElementById('gm-concurrency').value = getClampedIntegerSetting('gm_concurrency', CONFIG.defaults.concurrency, 1, 32);
        const similarity = getUrlSimilarityThreshold();
        document.getElementById('gm-url-similarity').value = Math.round(similarity * 100);
        document.getElementById('gm-max-cache-size').value = GM_getValue('gm_max_cache_size', CONFIG.defaults.maxCacheSize);
        document.getElementById('gm-cache-ttl').value = GM_getValue('gm_cache_ttl', CONFIG.defaults.cacheTTL);
        
        updateCacheStats();
        document.getElementById('gm-settings-panel').scrollTop = 0;
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
        toast.replaceChildren(getToastContent(text, type));
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
        
        toast.replaceChildren(getToastContent(text, type));
        
        // Auto-destroy when not loading
        if (type !== 'loading') {
            setTimeout(() => removeToast(id), 3000);
        }

        return id;
    }

    function getToastContent(text, type) {
        const content = document.createDocumentFragment();
        const icon = document.createElement('span');
        if (type === 'loading') icon.className = 'gm-spinner';
        else if (type === 'success') { icon.textContent = '✓'; icon.style.color = '#4ade80'; }
        else if (type === 'error') { icon.textContent = '✕'; icon.style.color = '#ef4444'; }
        const label = document.createElement('span');
        label.textContent = String(text);
        content.append(icon, label);
        return content;
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

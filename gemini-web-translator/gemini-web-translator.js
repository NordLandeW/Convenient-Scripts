// ==UserScript==
// @name         Gemini Webpage Translator
// @namespace    http://tampermonkey.net/
// @version      6.0
// @description  使用 Gemini API 翻译网页。
// @author       You
// @match        *://*/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_deleteValue
// @grant        GM_registerMenuCommand
// @connect      generativelanguage.googleapis.com
// @run-at       document-idle
// ==/UserScript==

(function() {
    'use strict';

    // ================= 配置 =================
    const CONFIG = {
        defaultModel: 'gemini-2.5-pro',
        endpoints: { base: 'https://generativelanguage.googleapis.com/v1beta/models' },
        cachePrefix: 'gm_cache_v7_',
        cacheMetaKey: 'gm_cache_meta',
        defaults: {
            maxCacheSize: 10 * 1024 * 1024, // 10MB
            cacheTTL: 7 * 24 * 60 * 60 * 1000, // 7天
        }
    };

    const state = {
        isTranslating: false,
        textNodeMap: new Map(),
        currentToastId: null
    };

    // ================= 样式表 =================
    const STYLES = `
        :root { --gm-primary: #8ab4f8; --gm-bg: #202124; --gm-text: #e8eaed; }
        #gm-translator-container { font-family: system-ui, sans-serif; z-index: 2147483647; position: fixed; top: 0; left: 0; }
        
        .gm-toast {
            position: fixed; top: 20px; right: 20px;
            background: rgba(32, 33, 36, 0.95);
            border: 1px solid rgba(255,255,255,0.1); color: var(--gm-text);
            padding: 12px 20px; border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.5); font-size: 14px;
            display: flex; align-items: center; gap: 10px;
            transform: translateY(-20px); opacity: 0; transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
            backdrop-filter: blur(8px);
        }
        .gm-toast.visible { transform: translateY(0); opacity: 1; }
        
        .gm-spinner {
            width: 16px; height: 16px; border: 2px solid rgba(255,255,255,0.2);
            border-top-color: var(--gm-primary); border-radius: 50%;
            animation: gm-spin 0.8s linear infinite;
        }
        @keyframes gm-spin { to { transform: rotate(360deg); } }

        /* Settings Overlay */
        #gm-settings-overlay {
            position: fixed; inset: 0; background: rgba(0,0,0,0.6);
            display: flex; justify-content: center; align-items: center;
            opacity: 0; pointer-events: none; transition: opacity 0.2s;
            backdrop-filter: blur(2px);
        }
        #gm-settings-overlay.open { opacity: 1; pointer-events: auto; }
        #gm-settings-panel {
            background: var(--gm-bg); border: 1px solid #3c4043;
            width: 400px; border-radius: 12px; padding: 24px;
            box-shadow: 0 12px 24px rgba(0,0,0,0.5);
            transform: scale(0.95); transition: transform 0.2s;
        }
        #gm-settings-overlay.open #gm-settings-panel { transform: scale(1); }

        .gm-input, .gm-select {
            width: 100%; background: #303134; border: 1px solid #3c4043;
            color: #fff; padding: 8px 12px; border-radius: 4px; font-size: 13px;
            margin-bottom: 15px; box-sizing: border-box; outline: none;
        }
        .gm-input:focus { border-color: var(--gm-primary); }
        .gm-btn { padding: 8px 16px; border-radius: 4px; font-size: 13px; cursor: pointer; border: none; margin-left: 8px; }
        .gm-btn-primary { background: var(--gm-primary); color: #202124; font-weight: 600; }
        .gm-btn-secondary { background: #303134; color: #fff; }
    `;

    // ================= 缓存管理 =================
    
    /**
     * 生成缓存键（使用SHA-256模拟）
     */
    function generateCacheKey(url) {
        // 使用更可靠的哈希方法，避免碰撞
        let hash = 0;
        for (let i = 0; i < url.length; i++) {
            const char = url.charCodeAt(i);
            hash = ((hash << 5) - hash) + char;
            hash = hash & hash;
        }
        return CONFIG.cachePrefix + Math.abs(hash).toString(36);
    }
    
    /**
     * 获取缓存元数据
     */
    function getCacheMeta() {
        const meta = GM_getValue(CONFIG.cacheMetaKey, '{}');
        try {
            return JSON.parse(meta);
        } catch {
            return {};
        }
    }
    
    /**
     * 保存缓存元数据
     */
    function saveCacheMeta(meta) {
        GM_setValue(CONFIG.cacheMetaKey, JSON.stringify(meta));
    }
    
    /**
     * 获取缓存（检查TTL）
     */
    function getCache(url) {
        const key = generateCacheKey(url);
        const meta = getCacheMeta();
        const entry = meta[key];

        if (!entry) return null;

        const ttl = GM_getValue('gm_cache_ttl', CONFIG.defaults.cacheTTL);
        const now = Date.now();

        // ttl <= 0 means "never expire"
        if (ttl > 0 && now - entry.timestamp > ttl) {
            deleteCache(key);
            return null;
        }

        const value = GM_getValue(key, null);

        // If metadata exists but value was removed, keep storage clean
        if (value == null) {
            deleteCache(key);
            return null;
        }

        return value;
    }
    
    /**
     * 设置缓存（检查大小限制）
     */
    function setCache(url, data) {
        const key = generateCacheKey(url);
        const meta = getCacheMeta();
        const dataSize = new Blob([data]).size;
        const maxSize = GM_getValue('gm_max_cache_size', CONFIG.defaults.maxCacheSize);

        // Compute current cache total size from metadata
        let totalSize = Object.values(meta).reduce(
            (sum, entry) => sum + (entry.size || 0),
            0
        );

        // If over limit, evict oldest entries until there is space
        while (totalSize + dataSize > maxSize && Object.keys(meta).length > 0) {
            const oldestKey = Object.keys(meta).reduce((oldest, k) =>
                !oldest || meta[k].timestamp < meta[oldest].timestamp ? k : oldest
            , null);

            if (!oldestKey) break;

            totalSize -= meta[oldestKey].size || 0;
            GM_deleteValue(oldestKey);
            delete meta[oldestKey];
        }

        // Save cache and metadata
        GM_setValue(key, data);
        meta[key] = {
            url: url,
            timestamp: Date.now(),
            size: dataSize
        };
        saveCacheMeta(meta);
    }
    
    /**
     * 删除指定缓存
     */
    function deleteCache(key) {
        const meta = getCacheMeta();
        GM_deleteValue(key);
        delete meta[key];
        saveCacheMeta(meta);
    }
    
    /**
     * 删除当前页面缓存
     */
    function clearCurrentPageCache() {
        const key = generateCacheKey(window.location.href);
        deleteCache(key);
    }
    
    /**
     * 清空所有缓存
     */
    function clearAllCache() {
        const meta = getCacheMeta();
        Object.keys(meta).forEach(key => GM_deleteValue(key));
        GM_deleteValue(CONFIG.cacheMetaKey);
    }
    
    /**
     * 获取缓存统计信息
     */
    function getCacheStats() {
        const meta = getCacheMeta();
        const entries = Object.values(meta);
        // Old entries from previous versions may not have size, default to 0 to keep stats stable
        const totalSize = entries.reduce((sum, entry) => sum + (entry.size || 0), 0);
        const count = entries.length;
        return { totalSize, count };
    }
    
    /**
     * 格式化字节大小
     */
    function formatBytes(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    // ================= 初始化 =================

    function init() {
        const style = document.createElement('style');
        style.textContent = STYLES;
        document.head.appendChild(style);
        
        createUI();
        
        GM_registerMenuCommand("🚀 开始翻译 (Alt+T)", startTranslationProcess);
        GM_registerMenuCommand("⚙️ 设置", openSettings);

        document.addEventListener('keydown', (e) => {
            if (e.altKey && e.key.toLowerCase() === 't') { e.preventDefault(); startTranslationProcess(); }
            if (e.altKey && e.key.toLowerCase() === 's') { e.preventDefault(); openSettings(); }
        });
        
        console.log('[Gemini Translator] Ready. V7 (Enhanced Cache).');
    }

    // ================= 核心逻辑：节点提取 =================

    /**
     * 提取页面中可翻译的文本节点
     * 过滤不可见、不可翻译的元素
     */
    function extractTextNodes() {
        const walker = document.createTreeWalker(
            document.body,
            NodeFilter.SHOW_TEXT,
            {
                acceptNode: function(node) {
                    const parent = node.parentElement;
                    if (!parent) return NodeFilter.FILTER_REJECT;
                    
                    // 标签黑名单
                    const tag = parent.tagName.toLowerCase();
                    if (['script', 'style', 'noscript', 'textarea', 'code', 'pre', 'svg', 'path', 'kbd', 'var'].includes(tag)) {
                        return NodeFilter.FILTER_REJECT;
                    }

                    // 检查不可翻译标记
                    if (parent.closest('[translate="no"], .notranslate')) {
                        return NodeFilter.FILTER_REJECT;
                    }

                    // 可见性检查
                    const style = window.getComputedStyle(parent);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                        return NodeFilter.FILTER_REJECT;
                    }

                    // 内容检查：过滤纯空白或纯数字
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

    // ================= CSV 处理 =================

    /**
     * 生成CSV格式文本和节点映射
     */
    function generateCsvAndMap(nodes) {
        state.textNodeMap.clear();
        let csv = "id,text\n";
        const tagCounters = {};

        nodes.forEach((node, index) => {
            const tag = node.parentElement.tagName.toUpperCase();
            if (!tagCounters[tag]) tagCounters[tag] = 0;
            const id = `${tag}_${tagCounters[tag]++}_${index}`; // Unique ID
            
            state.textNodeMap.set(id, node);
            
            // CSV 转义: 双引号转两个双引号，换行转空格
            const safeText = node.nodeValue.replace(/"/g, '""').replace(/[\r\n]+/g, ' ');
            csv += `${id},"${safeText}"\n`;
        });
        return csv;
    }

    /**
     * 应用CSV翻译结果到DOM节点
     */
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

    // ================= 翻译主流程 =================

    async function startTranslationProcess() {
        if (state.isTranslating) return;
        const apiKey = GM_getValue('gm_api_key', '');
        if (!apiKey) { showToast('请在设置中配置 API Key (Alt+S)', 'error'); openSettings(); return; }
    
        state.isTranslating = true;
        // 创建持久的 Toast
        state.currentToastId = showToast('正在分析页面结构...', 'loading', 0);
    
        try {
            const currentUrl = window.location.href;
            const cacheEnabled = GM_getValue('gm_cache_enable', true);
    
            // 先抽取节点并建立 ID -> 节点映射，缓存命中与 API 调用都依赖该映射
            const nodes = extractTextNodes();
            if (nodes.length === 0) throw new Error('未找到可翻译的文本');
            const csvInput = generateCsvAndMap(nodes);
    
            // 优先尝试从本地缓存恢复翻译结果
            if (cacheEnabled) {
                const cachedData = getCache(currentUrl);
    
                if (cachedData) {
                    updateToast(state.currentToastId, '加载本地缓存...', 'loading');
                    const count = applyCsvTranslation(cachedData);
                    updateToast(state.currentToastId, `⚡ 缓存加载成功 (${count} 节点)`, 'success');
                    state.isTranslating = false;
                    return;
                }
            }
    
            updateToast(state.currentToastId, `正在发送 ${nodes.length} 个文本段...`, 'loading');
    
            const model = GM_getValue('gm_model', CONFIG.defaultModel);
            const isDebug = GM_getValue('gm_debug', false);
    
            if (isDebug) console.log('CSV Input:', csvInput);
    
            // API 请求
            const prompt = `
            You are a CSV translator.
            Input format: id,"text content"
            Task: Translate "text content" to Simplified Chinese.
            Rules:
            1. Keep "id" exactly the same.
            2. Do NOT translate content inside HTML-like tags if any exist, but translate the text around them.
            3. Output valid CSV: id,"translated_text".
            4. If text is already Chinese, keep it as is.
            5. Escape double quotes with "".
            `;
    
            GM_xmlhttpRequest({
                method: "POST",
                url: `${CONFIG.endpoints.base}/${model}:generateContent`,
                headers: { "Content-Type": "application/json", "x-goog-api-key": apiKey },
                data: JSON.stringify({
                    system_instruction: { parts: { text: prompt } },
                    contents: [{ parts: [{ text: csvInput }] }]
                }),
                onload: (res) => {
                    try {
                        if (res.status !== 200) {
                            handleError(res);
                            return;
                        }
                        const data = JSON.parse(res.responseText || '{}');
                        let resultCsv = data.candidates?.[0]?.content?.parts?.[0]?.text;
    
                        if (!resultCsv) {
                            throw new Error('API 返回空内容');
                        }
    
                        resultCsv = resultCsv
                            .replace(/^```csv\s*/i, '')
                            .replace(/^```\s*/i, '')
                            .replace(/\s*```$/, '');
    
                        if (isDebug) console.log('CSV Output:', resultCsv);
    
                        const count = applyCsvTranslation(resultCsv);
    
                        // 保存缓存
                        if (cacheEnabled) {
                            setCache(currentUrl, resultCsv);
                        }
    
                        updateToast(state.currentToastId, `✅ 翻译完成 (${count} 节点)`, 'success');
                    } catch (e) {
                        console.error(e);
                        updateToast(state.currentToastId, '错误: ' + e.message, 'error');
                    } finally {
                        state.isTranslating = false;
                    }
                },
                onerror: (err) => {
                    console.error(err);
                    updateToast(state.currentToastId, '网络错误', 'error');
                    state.isTranslating = false;
                }
            });
    
        } catch (e) {
            console.error(e);
            updateToast(state.currentToastId, '错误: ' + e.message, 'error');
            state.isTranslating = false;
        }
    }

    function handleError(res) {
        const err = JSON.parse(res.responseText || '{}');
        const msg = err.error?.message || `Status ${res.status}`;
        updateToast(state.currentToastId, 'API 请求失败: ' + msg, 'error');
        state.isTranslating = false;
    }

    // ================= UI 组件 =================

    function createUI() {
        const div = document.createElement('div');
        div.id = 'gm-translator-container';
        div.innerHTML = `
            <div id="gm-settings-overlay">
                <div id="gm-settings-panel">
                    <h3 style="color:#fff; margin-top:0">Gemini Translator V7</h3>
                    
                    <label style="color:#aaa; font-size:12px">API Key</label>
                    <input type="password" id="gm-key" class="gm-input" placeholder="Paste Google AI Studio Key">
                    
                    <label style="color:#aaa; font-size:12px">Model</label>
                    <select id="gm-model" class="gm-select">
                        <option value="gemini-2.5-pro">Gemini 2.5 Pro (Stable)</option>
                        <option value="gemini-3.0-pro-preview">Gemini 3.0 Pro (New)</option>
                    </select>

                    <div style="margin-bottom:15px">
                        <label style="color:#fff; font-size:13px; display:flex; align-items:center; gap:5px;">
                            <input type="checkbox" id="gm-cache"> 启用本地缓存
                        </label>
                         <label style="color:#fff; font-size:13px; display:flex; align-items:center; gap:5px;">
                            <input type="checkbox" id="gm-debug"> Debug 模式
                        </label>
                    </div>

                    <label style="color:#aaa; font-size:12px">缓存大小限制</label>
                    <select id="gm-max-cache-size" class="gm-select">
                        <option value="5242880">5 MB</option>
                        <option value="10485760">10 MB</option>
                        <option value="20971520">20 MB</option>
                        <option value="52428800">50 MB</option>
                    </select>

                    <label style="color:#aaa; font-size:12px">缓存存活时间</label>
                    <select id="gm-cache-ttl" class="gm-select">
                        <option value="86400000">1 天</option>
                        <option value="259200000">3 天</option>
                        <option value="604800000">7 天</option>
                        <option value="2592000000">30 天</option>
                        <option value="0">永久</option>
                    </select>

                    <div id="gm-cache-stats" style="color:#aaa; font-size:12px; margin:10px 0; padding:8px; background:#303134; border-radius:4px;">
                        已使用: <span id="gm-cache-used">--</span> / <span id="gm-cache-limit">--</span>
                        (<span id="gm-cache-count">0</span> 个页面)
                    </div>

                    <div style="text-align:right; margin-top:15px;">
                        <button id="gm-clear-current" class="gm-btn" style="background:#522; color:#fcc; float:left; font-size:12px;">清除当前页</button>
                        <button id="gm-clear-all" class="gm-btn" style="background:#522; color:#fcc; float:left; margin-left:8px; font-size:12px;">清除全部</button>
                        <button id="gm-close" class="gm-btn gm-btn-secondary">取消</button>
                        <button id="gm-save" class="gm-btn gm-btn-primary">保存</button>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(div);

        document.getElementById('gm-save').onclick = () => {
            GM_setValue('gm_api_key', document.getElementById('gm-key').value.trim());
            GM_setValue('gm_model', document.getElementById('gm-model').value);
            GM_setValue('gm_cache_enable', document.getElementById('gm-cache').checked);
            GM_setValue('gm_debug', document.getElementById('gm-debug').checked);
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
            if (confirm('确定要清除所有缓存吗？')) {
                clearAllCache();
                updateCacheStats();
                showToast('所有缓存已清空', 'success');
            }
        };
    }

    /**
     * 更新缓存统计显示
     */
    function updateCacheStats() {
        const stats = getCacheStats();
        const maxSize = GM_getValue('gm_max_cache_size', CONFIG.defaults.maxCacheSize);
        
        document.getElementById('gm-cache-used').textContent = formatBytes(stats.totalSize);
        document.getElementById('gm-cache-limit').textContent = formatBytes(maxSize);
        document.getElementById('gm-cache-count').textContent = stats.count;
    }

    function openSettings() {
        document.getElementById('gm-key').value = GM_getValue('gm_api_key', '');
        document.getElementById('gm-model').value = GM_getValue('gm_model', CONFIG.defaultModel);
        document.getElementById('gm-cache').checked = GM_getValue('gm_cache_enable', true);
        document.getElementById('gm-debug').checked = GM_getValue('gm_debug', false);
        document.getElementById('gm-max-cache-size').value = GM_getValue('gm_max_cache_size', CONFIG.defaults.maxCacheSize);
        document.getElementById('gm-cache-ttl').value = GM_getValue('gm_cache_ttl', CONFIG.defaults.cacheTTL);
        
        updateCacheStats();
        document.getElementById('gm-settings-overlay').classList.add('open');
    }
    
    function closeSettings() {
        document.getElementById('gm-settings-overlay').classList.remove('open');
    }

    // ================= Toast 管理 =================
    
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
        
        // 当状态不再是loading时，设置定时器自动销毁
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
// ==UserScript==
// @name         QR Code Scanner
// @namespace    http://tampermonkey.net/
// @version      2.3
// @description  智能识别网页图片中的二维码，支持在线API和本地离线识别，带有设置页面。
// @author       nord
// @match        *://*/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setClipboard
// @grant        GM_addStyle
// @grant        GM_openInTab
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_registerMenuCommand
// @connect      api.2dcode.biz
// @connect      api.qrserver.com
// @connect      *
// @require      https://cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.js
// @run-at       document-end
// ==/UserScript==

(function() {
    'use strict';

    const CONSTANTS = {
        minImageSize: 50,
        iconSize: 32,
        zIndex: 2147483647
    };

    // Capabilities per online provider; used to decide whether URL/Upload is supported.
    const PROVIDER_CAPS = {
        caoliao: { fileurl: true, upload: false },
        qrserver: { fileurl: true, upload: true }
    };

    const Settings = {
        get provider() { return GM_getValue('qr_provider', 'caoliao'); },
        set provider(val) { GM_setValue('qr_provider', val); },
        get activationMode() { return GM_getValue('qr_activation_mode', 'always'); },
        set activationMode(val) { GM_setValue('qr_activation_mode', val); },
        // apiMethod: auto | fileurl | upload (only for online providers)
        get apiMethod() { return GM_getValue('qr_api_method', 'auto'); },
        set apiMethod(val) { GM_setValue('qr_api_method', val); }
    };

    const STYLES = `
        :root {
            --qr-primary: #007aff;
            --qr-bg: rgba(255, 255, 255, 0.95);
            --qr-shadow: 0 10px 40px rgba(0,0,0,0.15);
            --qr-border-radius: 16px;
        }
        .qr-scan-btn {
            position: absolute;
            width: ${CONSTANTS.iconSize}px;
            height: ${CONSTANTS.iconSize}px;
            background: rgba(255, 255, 255, 0.95);
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: ${CONSTANTS.zIndex};
            opacity: 0;
            transform: scale(0.8);
            pointer-events: none;
            visibility: hidden;
            transition: opacity 0.2s ease, transform 0.2s cubic-bezier(0.175, 0.885, 0.32, 1.275), visibility 0.2s;
            border: 1px solid rgba(0,0,0,0.1);
        }
        .qr-scan-btn.visible {
            opacity: 1;
            transform: scale(1);
            pointer-events: auto;
            visibility: visible;
        }
        .qr-scan-btn:hover {
            background: #fff;
            box-shadow: 0 6px 16px rgba(0,0,0,0.25);
            transform: scale(1.1);
        }
        .qr-scan-btn svg { width: 20px; height: 20px; fill: #333; }
        .qr-modal-window {
            position: fixed;
            top: 20%; left: 50%;
            transform: translate(-50%, 15px);
            width: 340px;
            background: var(--qr-bg);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border-radius: var(--qr-border-radius);
            box-shadow: var(--qr-shadow);
            z-index: ${CONSTANTS.zIndex + 1};
            padding: 20px;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            color: #333;
            border: 1px solid rgba(255,255,255,0.6);
            opacity: 0;
            visibility: hidden;
            transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
        }
        .qr-modal-window.active {
            opacity: 1;
            visibility: visible;
            transform: translate(-50%, 0);
        }
        .qr-modal-header {
            display: flex; justify-content: space-between; align-items: center;
            margin-bottom: 12px; padding-bottom: 12px;
            border-bottom: 1px solid rgba(0,0,0,0.06);
        }
        .qr-modal-title { font-weight: 600; font-size: 15px; color: #1a1a1a; }
        .qr-header-icons { display: flex; gap: 8px; }
        .qr-icon-btn {
            width: 24px; height: 24px; border-radius: 4px;
            display: flex; align-items: center; justify-content: center;
            cursor: pointer; color: #888; transition: all 0.2s;
        }
        .qr-icon-btn:hover { color: #333; background: rgba(0,0,0,0.05); }
        .qr-icon-btn svg { width: 18px; height: 18px; fill: currentColor; }
        .qr-modal-content {
            font-size: 13px; word-break: break-all; max-height: 160px; overflow-y: auto;
            background: #f2f4f7; padding: 12px; border-radius: 8px; margin-bottom: 15px;
            line-height: 1.5; color: #444; border: 1px solid rgba(0,0,0,0.02);
        }
        .qr-settings-label { display: block; font-size: 12px; color: #666; margin-bottom: 8px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
        .qr-radio-group { display: flex; flex-direction: column; gap: 8px; }
        .qr-radio-item {
            display: flex; align-items: center; padding: 10px; background: #f9f9f9;
            border-radius: 8px; cursor: pointer; border: 1px solid transparent; transition: all 0.2s;
        }
        .qr-radio-item:hover { background: #fff; border-color: #ddd; box-shadow: 0 2px 6px rgba(0,0,0,0.03); }
        .qr-radio-item.selected { background: #eef6ff; border-color: var(--qr-primary); color: var(--qr-primary); }
        .qr-radio-circle { width: 16px; height: 16px; border: 2px solid #ccc; border-radius: 50%; margin-right: 10px; position: relative; }
        .qr-radio-item.selected .qr-radio-circle { border-color: var(--qr-primary); }
        .qr-radio-item.selected .qr-radio-circle::after {
            content: ''; position: absolute; top: 3px; left: 3px; width: 6px; height: 6px;
            background: var(--qr-primary); border-radius: 50%;
        }
        .qr-radio-info { display: flex; flex-direction: column; }
        .qr-radio-title { font-size: 13px; font-weight: 600; }
        .qr-radio-desc { font-size: 11px; color: #888; margin-top: 2px; }
        .qr-radio-item.selected .qr-radio-desc { color: rgba(0, 122, 255, 0.7); }
        .qr-radio-item.disabled {
            cursor: not-allowed;
            opacity: 0.5;
            background: rgba(0, 0, 0, 0.02);
            color: inherit;
        }
        .qr-radio-item.disabled .qr-radio-circle {
            border-color: #d1d1d6;
            background: rgba(0, 0, 0, 0.05);
        }
        .qr-radio-item.disabled:hover {
            background: rgba(0, 0, 0, 0.02);
            border-color: transparent;
            box-shadow: none;
        }
        .qr-modal-actions { display: flex; gap: 8px; }
        .qr-btn {
            flex: 1; padding: 8px 0; border: none; border-radius: 8px;
            font-size: 13px; font-weight: 500; cursor: pointer; transition: all 0.2s;
        }
        .qr-btn-secondary { background: #ebedf0; color: #333; }
        .qr-btn-secondary:hover { background: #dbe0e6; }
        .qr-btn-primary { background: var(--qr-primary); color: #fff; }
        .qr-btn-primary:hover { background: #0062cc; }
        .qr-loading-wrap { text-align: center; color: #666; padding: 20px 0; font-size: 13px; }
        .qr-spinner {
            width: 20px; height: 20px; border: 2px solid #eee; border-top: 2px solid var(--qr-primary);
            border-radius: 50%; margin: 0 auto 10px; animation: qr-spin 0.8s linear infinite;
        }
        @keyframes qr-spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .qr-toast {
            position: fixed; bottom: 20px; right: 20px;
            background: rgba(0,0,0,0.75); color: white; padding: 10px 20px;
            border-radius: 8px; z-index: ${CONSTANTS.zIndex + 10};
            font-size: 14px; opacity: 0; transition: opacity 0.3s; pointer-events: none;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15); backdrop-filter: blur(4px);
        }
        .qr-toast.show { opacity: 1; }
    `;


    class QrDecoderService {
        constructor() {
            this.providers = {
                'caoliao': this.decodeOnline,
                'qrserver': this.decodeQrServer,
                'local': this.decodeLocal
            };
        }

        /**
         * Loads image bytes for decoding.
         *
         * Why: modern sites frequently use `blob:`/`data:` URLs, `<picture>/<source>` (WebP), or lazy-load placeholders.
         * A plain network GET can fail even though the image is visible in the page.
         * @param {string} imageUrl
         * @returns {Promise<Blob>}
         */
        async fetchImageBlob(imageUrl) {
            if (!imageUrl) throw "无法获取图片地址";

            // `fetch()` can read `blob:`/`data:` URLs that GM_xmlhttpRequest can't.
            if (/^(blob:|data:)/i.test(imageUrl)) {
                const r = await fetch(imageUrl);
                const b = await r.blob();
                if (!b || b.size === 0) throw "无法获取图片数据";
                return b;
            }

            return await this.fetchImageBlobByGmRequest(imageUrl);
        }

        /**
         * Uses Tampermonkey network stack to bypass CORS for cross-origin images.
         * Keeps redirects enabled and accepts non-200 success variants like 206.
         * @param {string} imageUrl
         * @returns {Promise<Blob>}
         */
        fetchImageBlobByGmRequest(imageUrl) {
            const doRequest = (referrer, allowRetry) => new Promise((resolve, reject) => {
                GM_xmlhttpRequest({
                    method: "GET",
                    url: imageUrl,
                    responseType: "blob",
                    redirect: "follow",
                    nocache: true,
                    revalidate: true,
                    // Make intent explicit; some environments behave differently for third-party resources.
                    anonymous: false,
                    headers: {
                        // Some CDNs vary formats by Accept; hint that WebP is fine.
                        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
                        // Hotlink protection is commonly implemented via referer checks.
                        // Tampermonkey treats some headers specially; `referer` tends to be more reliably applied than `Referer`.
                        "referer": referrer,
                        // Optional hints; unsupported headers will be ignored.
                        "origin": location.origin,
                        "user-agent": navigator.userAgent
                    },
                    onload: (res) => {
                        const blob = res.response;
                        const ok = (res.status >= 200 && res.status < 300) || res.status === 304;
                        const okOpaque = (res.status === 0 && blob && blob.size > 0);

                        if (!ok && !okOpaque) {
                            // Some CDNs only accept origin-level referrers (or normalize referrers internally).
                            if (allowRetry && res.status === 403) {
                                return doRequest(location.origin + '/', false).then(resolve, reject);
                            }
                            return reject(`图片下载失败 (${res.status || 'unknown'})`);
                        }

                        if (!blob || !(blob instanceof Blob) || blob.size === 0) return reject("无法获取图片数据");
                        resolve(blob);
                    },
                    onerror: () => reject("无法获取图片数据")
                });
            });

            return doRequest(location.href, true);
        }

        /**
         * Converts WebP to PNG for providers that may not accept WebP uploads.
         * Keeps original blob if conversion fails to avoid blocking the flow.
         * @param {Blob} blob
         * @param {string} sourceUrl
         * @returns {Promise<Blob>}
         */
        async maybeConvertWebpToPng(blob, sourceUrl) {
            const urlLooksWebp = /\.webp(?:[?#].*)?$/i.test(sourceUrl || '');
            const typeLooksWebp = (blob.type || '').toLowerCase().includes('image/webp');
            if (!urlLooksWebp && !typeLooksWebp) return blob;

            try {
                return await this.convertBlobToPng(blob);
            } catch (_) {
                return blob;
            }
        }

        /**
         * Converts an image blob into PNG using canvas.
         * Why: QRServer and similar endpoints are more likely to support PNG/JPEG than WebP.
         * @param {Blob} blob
         * @returns {Promise<Blob>}
         */
        async convertBlobToPng(blob) {
            const toPngFromCanvas = (canvas) => new Promise((resolve, reject) => {
                canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('toBlob failed'))), 'image/png');
            });

            if (typeof createImageBitmap === 'function') {
                const bmp = await createImageBitmap(blob);
                try {
                    const canvas = document.createElement('canvas');
                    canvas.width = bmp.width;
                    canvas.height = bmp.height;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(bmp, 0, 0);
                    return await toPngFromCanvas(canvas);
                } finally {
                    if (typeof bmp.close === 'function') bmp.close();
                }
            }

            // Fallback for environments without createImageBitmap.
            const blobUrl = URL.createObjectURL(blob);
            try {
                const img = await new Promise((resolve, reject) => {
                    const i = new Image();
                    i.onload = () => resolve(i);
                    i.onerror = () => reject(new Error('image load failed'));
                    i.src = blobUrl;
                });
                const canvas = document.createElement('canvas');
                canvas.width = img.width;
                canvas.height = img.height;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0);
                return await toPngFromCanvas(canvas);
            } finally {
                URL.revokeObjectURL(blobUrl);
            }
        }

        /**
         * Select provider/method based on user settings and capability.
         * Prefer URL method to minimize data transfer, fallback to upload when supported.
         */
        async decode(imageUrl) {
            const providerKey = Settings.provider;
            if (providerKey === 'local') {
                return this.decodeLocal(imageUrl);
            }
            const caps = (PROVIDER_CAPS[providerKey] || {});
            let method = Settings.apiMethod || 'auto';
            if (method === 'auto') {
                method = caps.fileurl ? 'fileurl' : (caps.upload ? 'upload' : 'fileurl');
            }
            if (method === 'upload' && !caps.upload) method = 'fileurl';
            if (method === 'fileurl' && !caps.fileurl) method = 'upload';

            if (providerKey === 'caoliao') {
                // CaoLiao only supports fileurl; upload path is intentionally ignored.
                return this.decodeOnline(imageUrl);
            }
            if (providerKey === 'qrserver') {
                return method === 'upload'
                    ? this.decodeQrServerUpload(imageUrl)
                    : this.decodeQrServer(imageUrl);
            }
            return this.decodeOnline(imageUrl);
        }

        decodeOnline(imageUrl) {
            return new Promise((resolve, reject) => {
                if (!imageUrl.startsWith('http')) {
                    return reject("在线API不支持此图片格式，请在设置中切换为【本地离线】模式。");
                }
                GM_xmlhttpRequest({
                    method: "GET",
                    url: `https://api.2dcode.biz/v1/read-qr-code?file_url=${encodeURIComponent(imageUrl)}`,
                    onload: (response) => {
                        try {
                            const res = JSON.parse(response.responseText);
                            if (res.code === 0 && res.data?.contents?.length > 0) resolve(res.data.contents[0]);
                            else reject(res.message || "未发现二维码");
                        } catch (e) { reject("API响应异常"); }
                    },
                    onerror: () => reject("网络请求失败")
                });
            });
        }

        /**
         * QRServer cloud decoding via fileurl.
         * Chosen to increase availability alongside the existing provider, without uploading local images.
         * Only supports HTTP(S) URLs; use local mode for data/blob or non-public images.
         * @param {string} imageUrl - The image URL that likely contains a QR code.
         * @returns {Promise<string>} Resolved with decoded text or rejected with a human-readable reason.
         */
        decodeQrServer(imageUrl) {
            return new Promise((resolve, reject) => {
                if (!/^https?:/i.test(imageUrl)) {
                    return reject("在线API不支持此图片格式，请在设置中切换为【本地离线】模式。");
                }
                GM_xmlhttpRequest({
                    method: "GET",
                    url: `https://api.qrserver.com/v1/read-qr-code/?fileurl=${encodeURIComponent(imageUrl)}&outputformat=json`,
                    onload: (response) => {
                        try {
                            const data = JSON.parse(response.responseText);
                            if (Array.isArray(data) && data.length > 0 && data[0]?.symbol?.length > 0) {
                                const sym = data[0].symbol[0];
                                if (sym.error) reject(sym.error);
                                else if (sym.data) resolve(sym.data);
                                else reject("未发现二维码");
                            } else {
                                reject("未发现二维码");
                            }
                        } catch (e) { reject("API响应异常"); }
                    },
                    onerror: () => reject("网络请求失败")
                });
            });
        }

        /**
         * QRServer file upload decoding.
         * Chosen for cases where public URL scanning is undesirable or impossible.
         * Enforces 1 MiB limit to respect API constraints.
         * @param {string} imageUrl
         * @returns {Promise<string>}
         */
        async decodeQrServerUpload(imageUrl) {
            if (!/^https?:/i.test(imageUrl)) {
                throw "仅支持上传来自 HTTP(S) 的图片；非公网图片请使用【本地离线】模式。";
            }

            let blob = await this.fetchImageBlob(imageUrl);
            blob = await this.maybeConvertWebpToPng(blob, imageUrl);

            if (blob.size > 1048576) {
                throw "文件超过 1 MiB，无法上传，请改用 URL 方式或本地离线";
            }

            const fd = new FormData();
            const ext = (blob.type || '').toLowerCase().includes('png') ? 'png' : 'jpg';
            fd.append("file", blob, `qr.${ext}`);

            const respText = await new Promise((resolve, reject) => {
                GM_xmlhttpRequest({
                    method: "POST",
                    url: "https://api.qrserver.com/v1/read-qr-code/?outputformat=json",
                    data: fd,
                    onload: (resp) => {
                        if (resp.status && (resp.status < 200 || resp.status >= 300)) {
                            return reject(`网络请求失败 (${resp.status})`);
                        }
                        resolve(resp.responseText);
                    },
                    onerror: () => reject("网络请求失败")
                });
            });

            try {
                const data = JSON.parse(respText);
                if (Array.isArray(data) && data.length > 0 && data[0]?.symbol?.length > 0) {
                    const sym = data[0].symbol[0];
                    if (sym.error) throw sym.error;
                    if (sym.data) return sym.data;
                }
                throw "未发现二维码";
            } catch (e) {
                if (typeof e === 'string') throw e;
                throw "API响应异常";
            }
        }

        async decodeLocal(imageUrl) {
            const blob = await this.fetchImageBlob(imageUrl);

            try {
                if (await this.isAnimatedBlob(blob)) throw "不支持动图图片格式";
            } catch (e) {
                if (typeof e === 'string') throw e;
            }

            const blobUrl = URL.createObjectURL(blob);
            try {
                const img = await new Promise((resolve, reject) => {
                    const i = new Image();
                    i.onload = () => resolve(i);
                    i.onerror = () => reject("图片加载失败");
                    i.src = blobUrl;
                });

                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                canvas.width = img.width;
                canvas.height = img.height;
                ctx.drawImage(img, 0, 0);

                const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
                const code = jsQR(imageData.data, imageData.width, imageData.height);
                if (!code) throw "未发现二维码";
                return code.data;
            } catch (e) {
                if (typeof e === 'string') throw e;
                throw "解析错误";
            } finally {
                URL.revokeObjectURL(blobUrl);
            }
        }

        async isAnimatedBlob(blob) {
            const type = (blob.type || '').toLowerCase();
            if (type.includes('image/gif')) return true;
            const head = new Uint8Array(await blob.slice(0, 65536).arrayBuffer());
            const dec = new TextDecoder('latin1');
            const s = dec.decode(head);
            if (type.includes('image/webp') || (s.includes('RIFF') && s.includes('WEBP'))) {
                if (s.includes('ANIM') || s.includes('ANMF')) return true;
            }
            if (type.includes('image/png') || s.charCodeAt(0) === 0x89 && s.slice(1,4) === 'PNG') {
                if (s.includes('acTL')) return true;
            }
            return false;
        }
    }

    const decoder = new QrDecoderService();

    const UI = {
        btn: null,
        resultModal: null,
        settingsModal: null,
        currentImg: null,
        hideTimer: null,
        isHoveringButton: false,
        isPluginActive: false,
        isUIInitialized: false,
        lastQPressTime: 0,
        toast: null,

        ensureUI() {
            if (this.isUIInitialized) return;
            GM_addStyle(STYLES);
            this.createScanButton();
            this.createResultModal();
            this.createSettingsModal();
            this.createToast();
            this.isUIInitialized = true;
        },

        init() {
            this.bindEvents();
            GM_registerMenuCommand("设置 / Settings", () => this.openSettings());
        },

        createToast() {
            const div = document.createElement('qr-scanner-ui');
            div.className = 'qr-toast';
            document.body.appendChild(div);
            this.toast = div;
        },

        showToast(msg) {
            this.ensureUI();
            this.toast.innerText = msg;
            this.toast.classList.add('show');
            setTimeout(() => this.toast.classList.remove('show'), 2000);
        },

        togglePluginActive() {
            this.isPluginActive = !this.isPluginActive;
            this.showToast(this.isPluginActive ? "二维码扫描已激活" : "二维码扫描已关闭");
            if (!this.isPluginActive) {
                this.btn.classList.remove('visible');
                this.currentImg = null;
            }
        },

        createScanButton() {
            const btn = document.createElement('qr-scanner-ui');
            btn.className = 'qr-scan-btn';
            btn.innerHTML = `<svg viewBox="0 0 24 24"><path d="M3 3h6v6H3V3zm2 2v2h2V5H5zm8-2h6v6h-6V3zm2 2v2h2V5h-2zM3 15h6v6H3v-6zm2 2v2h2v-2H5zm8 4h2v2h-2v-2zm2-2h2v2h-2v-2zm2 2h2v2h-2v-2zm-2-2h-2v-2h2v2zm4 0h2v2h-2v-2zm-2-4h-2v2h2v-2zm2 0h2v2h-2v-2z"/></svg>`;
            document.body.appendChild(btn);
            this.btn = btn;
            btn.addEventListener('mouseenter', () => {
                this.isHoveringButton = true;
                clearTimeout(this.hideTimer);
            });
            btn.addEventListener('mouseleave', () => {
                this.isHoveringButton = false;
                this.startHideTimer();
            });
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (this.currentImg) this.processImage(this.getImageUrl(this.currentImg));
            });
        },

        createResultModal() {
            const div = document.createElement('qr-scanner-ui');
            div.className = 'qr-modal-window';
            div.innerHTML = `
                <div class="qr-modal-header">
                    <span class="qr-modal-title">识别结果</span>
                    <div class="qr-header-icons">
                        <div class="qr-icon-btn" id="qr-set-btn" title="设置"><svg viewBox="0 0 24 24"><path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L3.16 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.04.64.09.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22-.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg></div>
                        <div class="qr-icon-btn" id="qr-cls-btn" title="关闭"><svg viewBox="0 0 24 24"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg></div>
                    </div>
                </div>
                <div class="qr-body-container"></div>
            `;
            document.body.appendChild(div);
            this.resultModal = div;
            div.querySelector('#qr-cls-btn').onclick = () => this.closeModal(div);
            div.querySelector('#qr-set-btn').onclick = (e) => { e.stopPropagation(); this.closeModal(div); this.openSettings(); };
        },

        createSettingsModal() {
            const div = document.createElement('qr-scanner-ui');
            div.className = 'qr-modal-window';
            div.innerHTML = `
                <div class="qr-modal-header">
                    <span class="qr-modal-title">设置 / Settings</span>
                    <div class="qr-header-icons">
                         <div class="qr-icon-btn" id="qr-set-close"><svg viewBox="0 0 24 24"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg></div>
                    </div>
                </div>
                <div style="margin-bottom:15px">
                    <label class="qr-settings-label">选择识别引擎</label>
                    <div class="qr-radio-group">
                        <div class="qr-radio-item" data-val="caoliao">
                            <div class="qr-radio-circle"></div>
                            <div class="qr-radio-info"><span class="qr-radio-title">在线 API (草料)</span><span class="qr-radio-desc">仅支持 URL 方式（file_url），公开 HTTP(S) 图片</span></div>
                        </div>
                        <div class="qr-radio-item" data-val="qrserver">
                            <div class="qr-radio-circle"></div>
                            <div class="qr-radio-info"><span class="qr-radio-title">在线 API (QRServer)</span><span class="qr-radio-desc">支持 URL（fileurl）与 文件上传（≤ 1 MiB），JSON 响应</span></div>
                        </div>
                        <div class="qr-radio-item" data-val="local">
                            <div class="qr-radio-circle"></div>
                            <div class="qr-radio-info"><span class="qr-radio-title">本地离线 (jsQR)</span><span class="qr-radio-desc">隐私安全，支持任何图片格式</span></div>
                        </div>
                    </div>
                </div>
                <div id="qr-online-options" style="margin-bottom:15px; display:none">
                    <label class="qr-settings-label">在线 API 方式</label>
                    <div class="qr-radio-group" id="qr-method-group">
                        <div class="qr-radio-item" data-group="method" data-val="auto">
                            <div class="qr-radio-circle"></div>
                            <div class="qr-radio-info"><span class="qr-radio-title">自动选择</span><span class="qr-radio-desc">优先使用 URL，不可用时切换上传</span></div>
                        </div>
                        <div class="qr-radio-item" data-group="method" data-val="fileurl">
                            <div class="qr-radio-circle"></div>
                            <div class="qr-radio-info"><span class="qr-radio-title">直接使用图片URL</span><span class="qr-radio-desc">无需上传，要求公开 HTTP(S) 图片</span></div>
                        </div>
                        <div class="qr-radio-item" data-group="method" data-val="upload">
                            <div class="qr-radio-circle"></div>
                            <div class="qr-radio-info"><span class="qr-radio-title">上传小文件</span><span class="qr-radio-desc">文件 ≤ 1 MiB，受接口支持限制</span></div>
                        </div>
                    </div>
                </div>
                <div style="margin-bottom:15px">
                    <label class="qr-settings-label">激活方式</label>
                    <div class="qr-radio-group">
                        <div class="qr-radio-item" data-group="mode" data-val="always">
                            <div class="qr-radio-circle"></div>
                            <div class="qr-radio-info"><span class="qr-radio-title">一直启用</span><span class="qr-radio-desc">鼠标悬停在二维码上自动显示按钮</span></div>
                        </div>
                        <div class="qr-radio-item" data-group="mode" data-val="shortcut">
                            <div class="qr-radio-circle"></div>
                            <div class="qr-radio-info"><span class="qr-radio-title">组合键激活 (Ctrl + QQ)</span><span class="qr-radio-desc">平时隐藏，按两下 Q 键激活/关闭</span></div>
                        </div>
                    </div>
                </div>
                <div class="qr-modal-actions"><button class="qr-btn qr-btn-primary" id="qr-save-set">保存</button></div>
            `;
            document.body.appendChild(div);
            this.settingsModal = div;
            const saveAndClose = () => this.closeModal(div);
            div.querySelector('#qr-set-close').onclick = saveAndClose;
            div.querySelector('#qr-save-set').onclick = saveAndClose;
            const items = div.querySelectorAll('.qr-radio-item');
            const allRadioItems = div.querySelectorAll('.qr-radio-item');
            allRadioItems.forEach(item => {
                item.addEventListener('click', () => {
                    const group = item.dataset.group;
                    if (group === 'mode') {
                        div.querySelectorAll('[data-group="mode"]').forEach(i => i.classList.remove('selected'));
                        item.classList.add('selected');
                        Settings.activationMode = item.dataset.val;
                        // Reset active state if switching modes
                        this.isPluginActive = false;
                        this.btn.classList.remove('visible');
                        return;
                    }
                    if (group === 'method') {
                        if (item.classList.contains('disabled')) return;
                        div.querySelectorAll('[data-group="method"]').forEach(i => i.classList.remove('selected'));
                        item.classList.add('selected');
                        Settings.apiMethod = item.dataset.val;
                        return;
                    }
                    // provider selection
                    div.querySelectorAll('.qr-radio-item:not([data-group])').forEach(i => i.classList.remove('selected'));
                    item.classList.add('selected');
                    Settings.provider = item.dataset.val;
                    this.updateOnlineOptionsUI();
                });
            });
        },

        /**
         * Toggle online options visibility and enforce capability-based disabling.
         * Forces a safe method when current selection is unsupported.
         */
        updateOnlineOptionsUI() {
            const provider = Settings.provider;
            const method = Settings.apiMethod || 'auto';
            const caps = PROVIDER_CAPS[provider] || {};
            const section = this.settingsModal.querySelector('#qr-online-options');
            const methodItems = this.settingsModal.querySelectorAll('#qr-method-group .qr-radio-item');

            // Show only for online providers
            const online = provider !== 'local';
            section.style.display = online ? 'block' : 'none';
            if (!online) return;

            // Update disabled state by capability
            methodItems.forEach(i => {
                i.classList.remove('disabled');
                if (i.dataset.val === 'fileurl' && caps.fileurl === false) i.classList.add('disabled');
                if (i.dataset.val === 'upload' && caps.upload === false) i.classList.add('disabled');
            });

            // Determine effective method if current is unsupported
            let effective = method;
            if (effective !== 'auto') {
                if (effective === 'fileurl' && caps.fileurl === false) effective = caps.upload ? 'upload' : 'auto';
                if (effective === 'upload' && caps.upload === false) effective = caps.fileurl ? 'fileurl' : 'auto';
            }
            if (effective !== method) Settings.apiMethod = effective;

            // Sync selected styles
            methodItems.forEach(i => i.classList.remove('selected'));
            const toSelect = this.settingsModal.querySelector(`#qr-method-group .qr-radio-item[data-val="${Settings.apiMethod}"]`);
            if (toSelect && !toSelect.classList.contains('disabled')) {
                toSelect.classList.add('selected');
            } else {
                const fallback = caps.fileurl ? 'fileurl' : (caps.upload ? 'upload' : 'auto');
                const fbNode = this.settingsModal.querySelector(`#qr-method-group .qr-radio-item[data-val="${fallback}"]`);
                if (fbNode) fbNode.classList.add('selected');
                Settings.apiMethod = fallback;
            }
        },

        bindEvents() {
            document.addEventListener('click', (e) => {
                if (this.resultModal.classList.contains('active') && !this.resultModal.contains(e.target)) {
                    this.closeModal(this.resultModal);
                }
                if (this.settingsModal.classList.contains('active') && !this.settingsModal.contains(e.target)) {
                    this.closeModal(this.settingsModal);
                }
            });

            document.addEventListener('keydown', (e) => {
                if (Settings.activationMode !== 'shortcut') return;
                if (e.key === 'q' || e.key === 'Q') {
                    if (e.ctrlKey) {
                        const now = Date.now();
                        if (now - this.lastQPressTime < 500) {
                            this.togglePluginActive();
                        }
                        this.lastQPressTime = now;
                    }
                }
            });

            document.addEventListener('mouseover', (e) => {
                const target = e.target;
                if (target.tagName === 'IMG') {
                    if (Settings.activationMode === 'shortcut' && !this.isPluginActive) return;
                    if (this.isLikelyAnimatedByUrl(target)) return;
                    const rect = target.getBoundingClientRect();
                    if (rect.width < CONSTANTS.minImageSize || rect.height < CONSTANTS.minImageSize) return;
                    this.ensureUI();
                    clearTimeout(this.hideTimer);
                    if (this.currentImg !== target) {
                        this.currentImg = target;
                        this.showButtonOn(target);
                    } else {
                        this.btn.classList.add('visible');
                    }
                } else if (this.isUIInitialized && this.btn.contains(target)) {
                    clearTimeout(this.hideTimer);
                } else {
                    this.startHideTimer();
                }
            }, true);
        },

        isLikelyAnimatedByUrl(img) {
            const src = (img.currentSrc || img.src || '').toLowerCase();
            if (/^data:image\/gif/.test(src)) return true;
            if (/\.(gif)(?:[?#].*)?$/.test(src)) return true;
            return false;
        },

        isPlaceholderDataUrl(url) {
            const s = (url || '').toLowerCase();
            return /^data:image\/gif/.test(s);
        },

        pickFromSrcset(srcset) {
            if (!srcset) return '';
            const parts = String(srcset)
                .split(',')
                .map(s => s.trim())
                .filter(Boolean);
            if (!parts.length) return '';
            const last = parts[parts.length - 1];
            return last.split(/\s+/)[0] || '';
        },

        /**
         * Finds the most usable URL for the actually-rendered image.
         * Why: `.src` may be a lazy-load placeholder while `.currentSrc` points to the real (often WebP) resource.
         */
        getImageUrl(img) {
            const candidates = [];
            const push = (v) => {
                if (typeof v !== 'string') return;
                const s = v.trim();
                if (s) candidates.push(s);
            };

            push(img?.currentSrc);
            push(img?.src);

            const attrs = [
                'data-src',
                'data-original',
                'data-url',
                'data-lazy-src',
                'data-actualsrc',
                'data-source',
                'data-img',
                'data-image',
                'data-large-image',
                'data-zoom-image'
            ];
            for (const a of attrs) push(img?.getAttribute?.(a));

            push(this.pickFromSrcset(img?.getAttribute?.('srcset')));
            push(this.pickFromSrcset(img?.getAttribute?.('data-srcset')));

            const normalized = candidates.map((u) => {
                if (/^(https?:|blob:|data:)/i.test(u)) return u;
                try { return new URL(u, location.href).toString(); } catch (_) { return u; }
            });

            return normalized.find(u => u && !this.isPlaceholderDataUrl(u)) || normalized[0] || '';
        },

        showButtonOn(img) {
            const rect = img.getBoundingClientRect();
            const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
            const scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;
            this.btn.style.top = (rect.top + scrollTop + 6) + 'px';
            this.btn.style.left = (rect.left + scrollLeft + 6) + 'px';
            this.btn.classList.add('visible');
        },

        startHideTimer() {
            if (this.isHoveringButton) return;
            clearTimeout(this.hideTimer);
            this.hideTimer = setTimeout(() => {
                this.btn.classList.remove('visible');
                this.currentImg = null;
            }, 200);
        },

        async processImage(url) {
            this.showLoading();
            this.openModal(this.resultModal);
            try {
                const result = await decoder.decode(url);
                this.showContent(result);
            } catch (err) {
                this.showError(err);
            }
        },

        showLoading() {
            this.resultModal.querySelector('.qr-body-container').innerHTML = `
                <div class="qr-loading-wrap">
                    <div class="qr-spinner"></div>
                    <div>${Settings.provider === 'local' ? '本地分析中...' : '云端识别中...'}</div>
                </div>`;
        },

        showContent(text) {
            const container = this.resultModal.querySelector('.qr-body-container');
            const isUrl = this.isValidUrl(text);
            container.innerHTML = `
                <div class="qr-modal-content">${this.escape(text)}</div>
                <div class="qr-modal-actions">
                    <button class="qr-btn qr-btn-secondary" id="qr-cp-res">复制内容</button>
                    ${isUrl ? `<button class="qr-btn qr-btn-primary" id="qr-go-res">跳转链接</button>` : ''}
                </div>`;
            const cpBtn = container.querySelector('#qr-cp-res');
            cpBtn.onclick = () => {
                GM_setClipboard(text);
                const old = cpBtn.innerText; cpBtn.innerText = '已复制';
                setTimeout(() => cpBtn.innerText = old, 1000);
            };
            if (isUrl) container.querySelector('#qr-go-res').onclick = () => GM_openInTab(text, {active:true});
        },

        showError(msg) {
            this.resultModal.querySelector('.qr-body-container').innerHTML =
                `<div class="qr-modal-content" style="color:#d93025;background:#fce8e6;">识别失败: ${msg}</div>`;
        },

        openSettings() {
            this.ensureUI();
            const provider = Settings.provider;
            const actMode = Settings.activationMode;
            const method = Settings.apiMethod;
            const items = this.settingsModal.querySelectorAll('.qr-radio-item');
            items.forEach(i => {
                if (i.dataset.group === 'mode') {
                    i.classList.toggle('selected', i.dataset.val === actMode);
                } else if (i.dataset.group === 'method') {
                    i.classList.toggle('selected', i.dataset.val === method);
                } else {
                    i.classList.toggle('selected', i.dataset.val === provider);
                }
            });
            this.updateOnlineOptionsUI();
            this.openModal(this.settingsModal);
        },

        openModal(modal) {
            this.resultModal.classList.remove('active');
            this.settingsModal.classList.remove('active');
            modal.classList.add('active');
        },
        closeModal(modal) { modal.classList.remove('active'); },
        isValidUrl(s) { try { return /^https?:/.test(new URL(s).protocol); } catch{ return false; } },
        escape(s) { return s.replace(/[&<>"']/g, m=>({'&':'&','<':'<','>':'>','"':'"',"'":'&#039;'}[m])); }
    };

    UI.init();

})();
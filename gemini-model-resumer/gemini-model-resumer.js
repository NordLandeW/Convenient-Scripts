// ==UserScript==
// @name         Gemini Model Resumer
// @namespace    http://tampermonkey.net/
// @version      0.7
// @description  自动恢复上次在 Gemini 中选择的模型
// @author       You
// @match        https://gemini.google.com/*
// @icon         https://www.google.com/s2/favicons?sz=64&domain=gemini.google.com
// @grant        none
// ==/UserScript==

(function() {
    'use strict';

    const STORAGE_KEY = 'gemini_preferred_model_v1';
    let isRestoring = false;
    let blockerOverlay = null;

    // 【新增】合法的模型名称关键词（支持中英文，匹配时全部转小写对比）
    const VALID_MODEL_KEYWORDS = ['快速', '思考', 'pro', 'flash', 'advanced'];

    // 获取纯净的模型名字（剔除多余的换行和描述）
    function getCoreModelName(text) {
        if (!text) return '';
        return text.split('\n')[0].replace(/\s+/g, ' ').trim();
    }

    // 寻找页面左上角的模型选择按钮
    function getModelButton() {
        const buttons = document.querySelectorAll('button');
        for (const btn of buttons) {
            const ariaLabel = (btn.getAttribute('aria-label') || '').toLowerCase();
            const dataTooltip = (btn.getAttribute('data-tooltip') || '').toLowerCase();
            if (ariaLabel.includes('模式选择器') || ariaLabel.includes('model selector') ||
                dataTooltip.includes('模式选择器') || dataTooltip.includes('model selector')) {
                return btn;
            }
        }
        return null;
    }

    // 判断是否是“新对话”页面
    function isNewChatPage() {
        const path = window.location.pathname;
        return path === '/' || path === '/app' || path === '/app/';
    }

    // ==========================================
    // 阻断用户输入模块
    // ==========================================
    function blockKeyboard(e) {
        e.stopPropagation();
        e.stopImmediatePropagation();
        e.preventDefault();
    }

    function enableInputBlocker() {
        if (!blockerOverlay) {
            blockerOverlay = document.createElement('div');
            // 全屏透明遮罩，鼠标变成 loading 状态，阻断所有点击操作
            blockerOverlay.style.cssText = 'position: fixed; inset: 0; z-index: 9999999; cursor: wait; background: transparent;';
        }
        document.body.appendChild(blockerOverlay);
        // 捕获阶段拦截键盘输入
        document.addEventListener('keydown', blockKeyboard, true);
        document.addEventListener('keypress', blockKeyboard, true);
        document.addEventListener('keyup', blockKeyboard, true);
        console.log('[Gemini Resumer] 锁定用户输入...');
    }

    function disableInputBlocker() {
        if (blockerOverlay && blockerOverlay.parentNode) {
            blockerOverlay.parentNode.removeChild(blockerOverlay);
        }
        document.removeEventListener('keydown', blockKeyboard, true);
        document.removeEventListener('keypress', blockKeyboard, true);
        document.removeEventListener('keyup', blockKeyboard, true);
        console.log('[Gemini Resumer] 释放用户输入...');
    }
    // ==========================================

    // 将焦点重新设置到输入框上的辅助方法
    function focusChatInput() {
        const selectors = [
            'rich-textarea [contenteditable="true"]',
            '.ql-editor',
            'textarea',
            '[role="textbox"][contenteditable="true"]'
        ];

        let inputEl = null;
        for (const selector of selectors) {
            inputEl = document.querySelector(selector);
            if (inputEl) break;
        }

        if (inputEl) {
            inputEl.focus();
            console.log('[Gemini Resumer] 输入框已重新聚焦');

            // 如果已有文字，将光标定位到最后
            if (inputEl.isContentEditable && inputEl.textContent.trim().length > 0) {
                try {
                    const selection = window.getSelection();
                    const range = document.createRange();
                    range.selectNodeContents(inputEl);
                    range.collapse(false);
                    selection.removeAllRanges();
                    selection.addRange(range);
                } catch (e) {
                    console.error('[Gemini Resumer] 光标移动失败', e);
                }
            }
        }
    }

    // 监听用户的手动点击，记录偏好
    document.addEventListener('click', (e) => {
        if (isRestoring) return;

        const menuItem = e.target.closest('[role^="menuitem"], [role="option"]');
        if (menuItem) {
            const selectedModel = getCoreModelName(menuItem.innerText || menuItem.textContent);

            // 【修改】验证点击的菜单项是否包含合法的模型关键词
            if (selectedModel) {
                const lowerCaseModel = selectedModel.toLowerCase();
                const isActualModel = VALID_MODEL_KEYWORDS.some(keyword => lowerCaseModel.includes(keyword));

                if (isActualModel) {
                    localStorage.setItem(STORAGE_KEY, selectedModel);
                    console.log('[Gemini Resumer] 已保存偏好模型:', selectedModel);
                    setTimeout(focusChatInput, 150);
                }
            }
        }
    }, true);

    // 核心恢复逻辑
    function triggerRestore() {
        if (isRestoring || !isNewChatPage()) return;

        const preferredModel = localStorage.getItem(STORAGE_KEY);
        if (!preferredModel) return;

        const btn = getModelButton();
        if (!btn || btn.disabled) return;

        const currentModel = getCoreModelName(btn.innerText || btn.textContent);

        // 如果模型不一致，开始自动切换
        if (currentModel && !preferredModel.includes(currentModel) && !currentModel.includes(preferredModel)) {
            console.log(`[Gemini Resumer] 正在恢复：[${currentModel}] -> [${preferredModel}]`);
            isRestoring = true;

            // 立即启用输入阻断
            enableInputBlocker();

            // 隐藏菜单样式，避免页面视觉闪烁
            const style = document.createElement('style');
            style.textContent = `[role="menu"], [role="listbox"], .mat-mdc-menu-panel { opacity: 0 !important; }`;
            document.head.appendChild(style);

            // 点击主按钮展开菜单 (注意: JS 代码的 .click() 能够穿透透明遮罩)
            btn.click();

            // 安全兜底(Failsafe): 如果 4 秒内没走完流程，强制重置状态，防止死锁
            const failSafeTimer = setTimeout(() => {
                if (isRestoring) {
                    isRestoring = false;
                    disableInputBlocker();
                    if (document.head.contains(style)) style.remove();
                    console.warn('[Gemini Resumer] 恢复操作异常，触发兜底释放！');
                }
            }, 4000);

            // 动态轮询等待菜单项加载出来（最多等待 2 秒）
            let attempts = 0;
            const checkMenu = setInterval(() => {
                attempts++;
                const radios = document.querySelectorAll('[role^="menuitem"], [role="option"]');

                if (radios.length > 0) {
                    clearInterval(checkMenu);
                    clearTimeout(failSafeTimer);
                    let switched = false;

                    for (const radio of radios) {
                        const radioText = getCoreModelName(radio.innerText || radio.textContent);
                        if (radioText.includes(preferredModel) || preferredModel.includes(radioText)) {
                            radio.click();
                            switched = true;
                            console.log('[Gemini Resumer] 自动恢复成功！');
                            break;
                        }
                    }

                    if (!switched) {
                        console.warn('[Gemini Resumer] 未找到匹配模型，关闭菜单');
                        btn.click();
                    }

                    // 延迟清理状态
                    setTimeout(() => {
                        style.remove();
                        isRestoring = false;
                        disableInputBlocker(); // 解除输入拦截
                        focusChatInput();      // 聚焦输入框
                    }, 300);

                } else if (attempts > 20) {
                    clearInterval(checkMenu);
                    clearTimeout(failSafeTimer);
                    btn.click();
                    style.remove();
                    isRestoring = false;
                    disableInputBlocker(); // 解除输入拦截
                    console.warn('[Gemini Resumer] 菜单加载超时');
                    focusChatInput();      // 超时也尝试聚焦
                }
            }, 100);
        }
    }

    // 初始化监听
    const initObserver = new MutationObserver(() => {
        if (getModelButton()) {
            initObserver.disconnect();
            setTimeout(triggerRestore, 500);
        }
    });
    initObserver.observe(document.body, { childList: true, subtree: true });

    // 路由监听
    let lastUrl = location.href;
    const routeObserver = new MutationObserver(() => {
        if (location.href !== lastUrl) {
            lastUrl = location.href;
            setTimeout(triggerRestore, 800);
        }
    });
    routeObserver.observe(document.body, { childList: true, subtree: true });

})();
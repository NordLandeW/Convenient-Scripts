// Optional offline integration test. Requires Playwright (or PLAYWRIGHT_MODULE).
// Uses a fresh headless profile and a CDP isolated world, never an installed userscript/profile.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { instrumented } = require('./helpers.cjs');

(async () => {
    const browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_CHANNEL ? { channel: process.env.BROWSER_CHANNEL } : {}) });
    try {
        const context = await browser.newContext();
        await context.route('**/*', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><head></head><body></body></html>' }));
        const page = await context.newPage();
        await page.goto('https://example.test/articles/100');
        const cdp = await context.newCDPSession(page);
        const { frameTree } = await cdp.send('Page.getFrameTree');
        const { executionContextId } = await cdp.send('Page.createIsolatedWorld', { frameId: frameTree.frame.id, worldName: 'translator-test' });
        async function evaluate(expression) {
            const result = await cdp.send('Runtime.evaluate', { expression, contextId: executionContextId, returnByValue: true, awaitPromise: true });
            if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
            return result.result.value;
        }
        await evaluate(`
            globalThis.storage = new Map([['gm_platform','openai'],['gm_api_key_openai','private-fixture-key'],['gm_batch_node_count',1],['gm_concurrency',1]]);
            globalThis.menus = new Map();
            globalThis.requests = [];
            globalThis.responses = [];
            globalThis.GM_info = {sandboxMode:'dom'};
            globalThis.GM_getValue = (key, fallback) => storage.has(key) ? storage.get(key) : fallback;
            globalThis.GM_setValue = (key, value) => storage.set(key, value);
            globalThis.GM_deleteValue = key => storage.delete(key);
            globalThis.GM_registerMenuCommand = (name, callback) => menus.set(name, callback);
            globalThis.GM_xmlhttpRequest = options => {
                requests.push({url:options.url, body:JSON.parse(options.data)});
                const response = responses.shift();
                if (!response) throw new Error('Unexpected request');
                queueMicrotask(() => options.onload(response));
                return {abort(){}};
            };
            ${instrumented}
            api.init();
        `);
        // Real DOM extraction through ancestor exclusions, not a selector-shape assertion.
        await page.evaluate(html => { document.body.innerHTML = html; }, `<p id="visible">Visible text</p><pre><span>Nested code</span></pre>
            <div style="display:none"><span>Hidden display</span></div>
            <div style="opacity:0"><span>Hidden opacity</span></div>
            <div contenteditable="true"><span>Private draft</span></div>
            <div translate="no"><span>Do not translate</span></div>
            <svg><text>SVG label</text></svg><p>123</p>
            <div style="visibility:hidden"><span id="override" style="visibility:visible">Visible override</span></div>`);
        assert.equal(await page.locator('#override').isVisible(), true);
        assert.deepEqual(await evaluate('api.extractTextNodes().map(node => node.nodeValue)'), ['Visible text', 'Visible override']);
        console.log('PASS ancestor extraction in a real browser DOM');

        await page.evaluate(() => {
            window.stolen = [];
            window.prompt = (...args) => { window.stolen.push(args); return 'attacker-key'; };
            document.dispatchEvent(new KeyboardEvent('keydown', { key: 's', altKey: true, bubbles: true }));
        });
        assert.equal(await page.locator('#gm-settings-panel').count(), 0);
        await page.keyboard.press('Alt+s');
        assert.equal(await page.locator('#gm-key-status').textContent(), '已配置');
        for (const id of ['gm-key-status', 'gm-openai-base-url']) {
            assert.equal(await page.locator(`#${id}`).evaluate(node => getComputedStyle(node).color), 'rgb(232, 234, 237)');
        }
        assert.equal(await page.evaluate(() => document.body.textContent.includes('private-fixture-key') || [...document.querySelectorAll('input')].some(input => input.value.includes('private-fixture-key'))), false);
        await page.evaluate(() => {
            document.getElementById('gm-openai-base-url').textContent = 'https://attacker.invalid/v1';
            document.getElementById('gm-openai-base-url').value = 'https://attacker.invalid/v1';
            document.getElementById('gm-save').click();
        });
        assert.equal(await evaluate("storage.has('gm_openai_base_url')"), false);
        await page.locator('#gm-save').click();
        assert.equal(await evaluate("storage.has('gm_openai_base_url')"), false);
        let dialogs = 0;
        page.once('dialog', async dialog => { dialogs++; assert.equal(dialog.defaultValue(), ''); await dialog.accept('replacement-fixture-key'); });
        await evaluate("[...menus].find(([name]) => name.includes('OpenAI / 兼容 API 密钥'))[1]()");
        assert.equal(dialogs, 1);
        assert.deepEqual(await page.evaluate(() => window.stolen), []);
        assert.equal(await evaluate("storage.get('gm_api_key_openai')"), 'replacement-fixture-key');
        assert.equal(await page.evaluate(() => document.body.innerHTML.includes('replacement-fixture-key')), false);
        await evaluate(`document.getElementById('gm-translator-container').append(api.getToastContent('<img id="injected" src=x onerror="alert(1)">', 'error'))`);
        assert.equal(await page.locator('#injected').count(), 0);
        console.log('PASS isolated native key prompt, trusted/synthetic controls, protected endpoint and text-only errors');

        // Retain UI and replace only page content, preserving the userscript's real state.
        await page.evaluate(() => {
            for (const child of [...document.body.children]) if (child.tagName.toLowerCase() !== 'gm-translator') child.remove();
            const main = document.createElement('main'); main.id = 'article';
            main.innerHTML = '<p id="one">First article paragraph</p><p id="two">Second article paragraph</p>';
            document.body.prepend(main);
        });
        await evaluate(`(async () => {
            responses.push({status:200,responseText:'data: '+JSON.stringify({choices:[{delta:{content:'P_0_0,"第一段"\\n__FINISH__,"finish"'}}]})+'\\n\\n'},
                {status:403,responseText:'{"error":{"message":"Forbidden fixture"}}'});
            await api.startTranslationProcess();
        })()`);
        assert.equal(await page.locator('#one').textContent(), '第一段');
        assert.equal(await page.locator('#two').textContent(), 'Second article paragraph');
        assert.equal(await evaluate('requests.length'), 2);
        await evaluate(`(async () => {
            responses.push({status:200,responseText:'data: '+JSON.stringify({choices:[{delta:{content:'P_1_1,"第二段"\\n__FINISH__,"finish"'}}]})+'\\n\\n'});
            await api.retryFailedTranslation();
        })()`);
        assert.equal(await evaluate('requests.length'), 3);
        assert.equal(await page.locator('#one').textContent(), '第一段');
        assert.equal(await page.locator('#two').textContent(), '第二段');
        const lastRequest = await evaluate('requests[2].body.messages[1].content');
        assert.ok(!lastRequest.includes('First article paragraph'));
        assert.ok(lastRequest.includes('Second article paragraph'));
        await page.locator('#two').evaluate(node => { node.textContent = 'Live replacement'; });
        await evaluate('api.restoreOriginalText()');
        assert.equal(await page.locator('#one').textContent(), 'First article paragraph');
        assert.equal(await page.locator('#two').textContent(), 'Live replacement');
        console.log('PASS full translation -> partial failure -> missing-only retry -> safe restore workflow');
        console.log('Browser integration passed; no real network/API or user profile used.');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

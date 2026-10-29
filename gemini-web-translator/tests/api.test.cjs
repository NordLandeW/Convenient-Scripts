const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// Exercise the userscript's actual functions without initializing a webpage.
const source = fs.readFileSync(path.join(__dirname, '..', 'gemini-web-translator.js'), 'utf8');
const instrumented = source.replace('    if (isExcludedContext()) {', `
    globalThis.api = {
        CONFIG, state, getPlatformModelSettings, getOpenAIProtocol, getOpenAIServiceTier,
        normalizeOpenAIBaseUrl, createStreamRequestOptions, extractStreamTextFromPayload,
        consumeSSEPayload, flushSSEBuffer, createTranslationBatches, createProgressState,
        translateSingleBatch, translateAllBatches
    };
    return;
    if (isExcludedContext()) {`);

function load(values = {}, request = () => { throw new Error('Unexpected request'); }) {
    const storage = new Map(Object.entries(values));
    const context = vm.createContext({
        URL, TextDecoder, setTimeout, clearTimeout, console,
        GM_getValue: (key, fallback) => storage.has(key) ? storage.get(key) : fallback,
        GM_setValue: (key, value) => storage.set(key, value),
        GM_xmlhttpRequest: request
    });
    vm.runInContext(instrumented, context);
    return { api: context.api, storage };
}

function requestBody(api, platform = 'openai', model = 'test-model') {
    const options = api.createStreamRequestOptions(platform, 'test-key', model, 'Translate', 'n1,"Hello"');
    return { options, body: JSON.parse(options.data) };
}

test('OpenAI Chat Completions sends messages, bearer auth, streaming and the selected tier', () => {
    const { api } = load({ gm_openai_service_tier: 'priority' });
    const { options, body } = requestBody(api);
    assert.equal(options.url, 'https://api.openai.com/v1/chat/completions');
    assert.equal(options.headers.Authorization, 'Bearer test-key');
    assert.equal(options.responseType, 'stream');
    assert.deepEqual(body, {
        model: 'test-model',
        messages: [{ role: 'system', content: 'Translate' }, { role: 'user', content: 'n1,"Hello"' }],
        stream: true, service_tier: 'priority', store: false
    });
});

test('Responses uses instructions/input and supports a custom API base', () => {
    const { api } = load({
        gm_openai_protocol: 'responses', gm_openai_service_tier: 'flex',
        gm_openai_base_url: 'https://gateway.example/api/v1/'
    });
    const { options, body } = requestBody(api);
    assert.equal(options.url, 'https://gateway.example/api/v1/responses');
    assert.deepEqual(body, {
        model: 'test-model', instructions: 'Translate', input: 'n1,"Hello"',
        stream: true, store: false, service_tier: 'flex'
    });
});

test('both OpenAI protocols forward each selected tier, or omit it for compatibility', () => {
    for (const protocol of ['chat', 'responses']) {
        for (const tier of ['', 'auto', 'default', 'flex', 'priority', 'fast', 'scale']) {
            const { api } = load({ gm_openai_protocol: protocol, gm_openai_service_tier: tier });
            const { body } = requestBody(api);
            assert.equal(body.service_tier, tier || undefined);
        }
    }
    const { api, storage } = load({ gm_openai_protocol: 'responses', gm_openai_service_tier: 'ultrafast' });
    assert.equal(requestBody(api).body.service_tier, 'ultrafast');
    storage.set('gm_openai_protocol', 'chat');
    assert.equal(requestBody(api).body.service_tier, 'auto');
    storage.set('gm_openai_service_tier', 'invalid');
    storage.set('gm_openai_protocol', 'invalid');
    assert.equal(requestBody(api).body.service_tier, 'auto');
    assert.match(requestBody(api).options.url, /chat\/completions$/);
});

test('API base accepts root URLs, existing endpoint suffixes and local proxies', () => {
    const { api } = load();
    for (const input of ['', ' https://api.openai.com/ ', 'https://api.openai.com/v1/',
        'https://api.openai.com/v1/chat/completions', 'https://api.openai.com/v1/responses/']) {
        assert.equal(api.normalizeOpenAIBaseUrl(input), 'https://api.openai.com/v1');
    }
    assert.equal(api.normalizeOpenAIBaseUrl('http://localhost:8080/custom/v1'), 'http://localhost:8080/custom/v1');
    for (const input of ['bad url', 'file:///tmp/api', 'https://user:secret@example.com/v1',
        'https://example.com/v1?key=secret', 'https://example.com/v1#fragment']) {
        assert.throws(() => api.normalizeOpenAIBaseUrl(input));
    }
});

test('OpenAI options do not leak into Gemini or OpenRouter requests', () => {
    const { api } = load({ gm_openai_protocol: 'responses', gm_openai_service_tier: 'priority', gm_reasoning_enable: false });
    const gemini = requestBody(api, 'gemini');
    assert.match(gemini.options.url, /:streamGenerateContent\?alt=sse$/);
    assert.equal(gemini.options.headers['x-goog-api-key'], 'test-key');
    assert.deepEqual(gemini.body.contents, [{ parts: [{ text: 'n1,"Hello"' }] }]);
    const router = requestBody(api, 'openrouter');
    assert.equal(router.options.url, 'https://openrouter.ai/api/v1/chat/completions');
    assert.deepEqual(router.body.reasoning, { enabled: false });
    assert.ok(router.body.messages);
    for (const { body } of [gemini, router]) {
        assert.equal(body.service_tier, undefined);
        assert.equal(body.instructions, undefined);
    }
});

test('legacy models migrate only for their platform and platform settings take precedence', () => {
    const { api, storage } = load({ gm_platform: 'gemini', gm_model: 'gemini-2.5-pro', gm_model_input: 'my-gemini' });
    assert.equal(api.getPlatformModelSettings('gemini').customModel, 'my-gemini');
    assert.equal(api.getPlatformModelSettings('openai').customModel, '');
    assert.equal(api.getPlatformModelSettings('openai').model, '');
    storage.set('gm_model_input_gemini', 'new-gemini');
    storage.set('gm_model_openai', 'gpt-4.1-mini');
    assert.equal(api.getPlatformModelSettings('gemini').customModel, 'new-gemini');
    assert.equal(api.getPlatformModelSettings('openai').model, 'gpt-4.1-mini');
});

test('OpenAI reasoning uses the selected protocol schema without changing the model or service tier', () => {
    for (const protocol of ['chat', 'responses']) {
        for (const [model, effort] of [
            ['gpt-6-astra', 'max'], ['gpt-6.1-sol', 'xhigh'],
            ['gpt-6.1-terra', 'none'], ['gpt-6-luna', 'low']
        ]) {
            const { api } = load({ gm_openai_protocol: protocol, gm_openai_service_tier: 'priority', gm_reasoning_effort_openai: effort });
            const { body } = requestBody(api, 'openai', model);
            assert.equal(body.model, model);
            assert.equal(body.service_tier, 'priority');
            assert.equal(body.reasoning_effort, protocol === 'chat' ? effort : undefined);
            assert.deepEqual(body.reasoning, protocol === 'responses' ? { effort } : undefined);
        }
    }
});

test('default reasoning leaves native API defaults intact', () => {
    for (const protocol of ['chat', 'responses']) {
        const { api } = load({ gm_openai_protocol: protocol });
        const { body } = requestBody(api, 'openai', 'gpt-6-astra');
        assert.equal(body.reasoning, undefined);
        assert.equal(body.reasoning_effort, undefined);
    }
    const { api } = load();
    assert.equal(requestBody(api, 'gemini', 'gemini-4-argon').body.generationConfig, undefined);
});

test('Gemini thinking intensity is sent under generationConfig for Flash, Argon and Pro', () => {
    const { api } = load({ gm_reasoning_effort_gemini: 'high' });
    for (const model of ['gemini-3.8-flash', 'gemini-4-argon', 'gemini-3.1-pro-preview']) {
        const { body } = requestBody(api, 'gemini', model);
        assert.deepEqual(body.generationConfig, { thinkingConfig: { thinkingLevel: 'high' } });
        assert.equal(body.reasoning, undefined);
        assert.equal(body.reasoning_effort, undefined);
    }
});

test('unsupported efforts are omitted instead of causing native provider request errors', () => {
    for (const [platform, model, effort] of [
        ['openai', 'gpt-6-astra', 'none'], ['openai', 'gpt-6.1-sol', 'none'],
        ['openai', 'gpt-6.1-sol-20261026', 'none'], ['openai', 'gpt-6-luna', 'minimal'],
        ['openai', 'gpt-4.1-mini', 'high'], ['gemini', 'gemini-3.8-flash', 'minimal'],
        ['gemini', 'gemini-4-argon', 'none'], ['gemini', 'gemini-2.5-pro', 'high']
    ]) {
        const { api } = load({ [`gm_reasoning_effort_${platform}`]: effort });
        const { body } = requestBody(api, platform, model);
        assert.equal(body.generationConfig, undefined);
        assert.equal(body.reasoning_effort, undefined);
        assert.equal(body.reasoning, undefined);
    }
    const { api } = load({ gm_reasoning_effort_gemini: 'minimal' });
    assert.deepEqual(requestBody(api, 'gemini', 'gemini-3.5-flash-lite').body.generationConfig,
        { thinkingConfig: { thinkingLevel: 'minimal' } });
});

test('OpenRouter migrates its old switch, accepts explicit effort, and respects mandatory reasoning', () => {
    const { api, storage } = load({ gm_reasoning_enable: false });
    assert.deepEqual(requestBody(api, 'openrouter').body.reasoning, { enabled: false });
    storage.set('gm_reasoning_effort_openrouter', 'high');
    assert.deepEqual(requestBody(api, 'openrouter').body.reasoning, { effort: 'high' });
    storage.set('gm_reasoning_effort_openrouter', 'none');
    assert.deepEqual(requestBody(api, 'openrouter').body.reasoning, { enabled: false });
    assert.equal(requestBody(api, 'openrouter', 'google/gemini-4-argon').body.reasoning, undefined);
    assert.equal(requestBody(api, 'openrouter', 'openai/gpt-6-astra').body.reasoning, undefined);
});

test('reasoning intensity settings are isolated by platform', () => {
    const { api } = load({ gm_reasoning_effort_gemini: 'low', gm_reasoning_effort_openai: 'max', gm_reasoning_effort_openrouter: 'medium' });
    assert.equal(requestBody(api, 'gemini', 'gemini-3.8-flash').body.generationConfig.thinkingConfig.thinkingLevel, 'low');
    assert.equal(requestBody(api, 'openai', 'gpt-6-luna').body.reasoning_effort, 'max');
    assert.deepEqual(requestBody(api, 'openrouter').body.reasoning, { effort: 'medium' });
});

test('Gemini thought summaries cannot enter the translated CSV', () => {
    const { api } = load();
    const payload = JSON.stringify({ candidates: [{ content: { parts: [
        { thought: true, text: 'n1,"Do not apply this thought"\n' },
        { text: 'n1,"译文"\n' }, { thoughtSignature: 'opaque-signature' }
    ] } }] });
    assert.equal(api.extractStreamTextFromPayload(payload, 'gemini'), 'n1,"译文"\n');
});

function sse(data, newline = '\n') {
    return `event: ${data.type || 'message'}${newline}data: ${JSON.stringify(data)}${newline}${newline}`;
}

const csv = 'n1,"你好"\n__FINISH__,"finish"';
function responseEvents(text = csv) {
    return [
        { type: 'response.created', response: { status: 'in_progress' } },
        { type: 'response.reasoning_summary_text.delta', delta: 'Do not translate this reasoning' },
        { type: 'response.output_text.delta', delta: text.slice(0, 7) },
        { type: 'response.output_text.delta', delta: text.slice(7) },
        { type: 'response.output_text.done', text },
        { type: 'response.completed', response: { output: [{ content: [{ type: 'output_text', text }] }] } }
    ];
}

test('Responses reads only text deltas; completed snapshots and reasoning are not duplicated', () => {
    const { api } = load();
    const text = responseEvents().map(event => api.extractStreamTextFromPayload(JSON.stringify(event), 'openai')).join('');
    assert.equal(text, csv);
});

test('Responses reports failed, incomplete, refusal and top-level error events', () => {
    const { api } = load();
    for (const [event, message] of [
        [{ type: 'error', message: 'Rate limited' }, 'Rate limited'],
        [{ error: { message: 'Invalid API key' } }, 'Invalid API key'],
        [{ type: 'response.failed', response: { error: { message: 'Model unavailable' } } }, 'Model unavailable'],
        [{ type: 'response.incomplete', response: { incomplete_details: { reason: 'max_output_tokens' } } }, 'max_output_tokens'],
        [{ type: 'response.refusal.delta', delta: 'Cannot comply' }, 'Cannot comply']
    ]) {
        assert.throws(() => api.extractStreamTextFromPayload(JSON.stringify(event), 'openai'), new RegExp(message));
    }
});

test('Chat Completions reports truncated or filtered responses', () => {
    const { api } = load();
    for (const finish_reason of ['length', 'content_filter', 'error']) {
        assert.throws(() => api.extractStreamTextFromPayload(JSON.stringify({ choices: [{ finish_reason }] }), 'openai'));
    }
});

test('SSE survives every possible split, including CRLF pairs, and ignores DONE', () => {
    const { api } = load();
    for (const newline of ['\n', '\r\n', '\r']) {
        const wire = responseEvents().map(event => sse(event, newline)).join('') + `data: [DONE]${newline}${newline}`;
        for (let split = 0; split <= wire.length; split++) {
            const parser = { buffer: '' };
            let output = '';
            const consume = payload => { output += api.extractStreamTextFromPayload(payload, 'openai'); };
            api.consumeSSEPayload(wire.slice(0, split), parser, consume);
            api.consumeSSEPayload(wire.slice(split), parser, consume);
            api.flushSSEBuffer(parser, consume);
            assert.equal(output, csv, `newline ${JSON.stringify(newline)}, split ${split}`);
        }
    }
});

function prepareBatch(api) {
    const node = { nodeValue: 'Hello' };
    api.state.isTranslating = true;
    api.state.activeTranslationRunId = 1;
    api.state.textNodeMap.set('n1', node);
    api.state.translationProgress = api.createProgressState(1, 1, 1);
    return { node, batch: api.createTranslationBatches([{ id: 'n1', text: 'Hello' }], 10)[0] };
}

for (const protocol of ['chat', 'responses']) {
    for (const transport of ['readable', 'progress', 'load']) {
        test(`${protocol}: ${transport} transport applies streamed CSV and completes exactly once`, async () => {
            const events = protocol === 'responses' ? responseEvents() : [
                { choices: [{ delta: { content: csv.slice(0, 7) } }] },
                { choices: [{ delta: { content: csv.slice(7) }, finish_reason: 'stop' }] }
            ];
            const wire = events.map(event => sse(event, '\r\n')).join('');
            const { api } = load({ gm_openai_protocol: protocol }, options => {
                queueMicrotask(() => {
                    if (transport === 'readable') {
                        const bytes = Buffer.from(wire);
                        let offset = 0;
                        options.onloadstart({ status: 200, response: { getReader: () => ({
                            // Single bytes also split multi-byte Chinese characters.
                            read: async () => offset < bytes.length
                                ? { value: bytes.subarray(offset, ++offset), done: false } : { done: true },
                            releaseLock() {}
                        }) } });
                    } else {
                        if (transport === 'progress') {
                            for (let end = 1; end < wire.length; end += 13) {
                                options.onprogress({ responseText: wire.slice(0, end) });
                            }
                        }
                        options.onload({ status: 200, responseText: wire });
                    }
                });
                return { abort() {} };
            });
            const { node, batch } = prepareBatch(api);
            await api.translateSingleBatch({ runId: 1, batch, platform: 'openai', apiKey: 'test-key', model: 'test-model', prompt: 'Translate' });
            assert.equal(node.nodeValue, '你好');
            assert.equal(api.state.originalTexts.get(node), 'Hello');
            assert.equal(batch.resultLines.join('\n'), csv);
            assert.equal(api.state.translationProgress.finishedBatches, 1);
            assert.equal(api.state.translationProgress.updatedNodes, 1);
            assert.equal(api.state.activeRequests.size, 0);
        });
    }
}

test('a Responses error after the CSV finish marker rejects and the existing retry path restores the node', async () => {
    let requests = 0;
    const { api } = load({ gm_openai_protocol: 'responses' }, options => {
        const attempt = ++requests;
        queueMicrotask(() => options.onload({
            status: 200,
            responseText: attempt === 1
                ? sse({ type: 'response.output_text.delta', delta: 'n1,"错误译文"\n__FINISH__,"finish"\n' })
                    + sse({ type: 'response.incomplete', response: { incomplete_details: { reason: 'max_output_tokens' } } })
                : responseEvents('__FINISH__,"finish"\n').map(event => sse(event)).join('')
        }));
        return { abort() {} };
    });
    const { node, batch } = prepareBatch(api);
    const result = await api.translateAllBatches({ runId: 1, platform: 'openai', apiKey: 'test-key', model: 'test-model', prompt: 'Translate', batches: [batch] });
    assert.equal(requests, 2);
    assert.equal(node.nodeValue, 'Hello');
    assert.equal(result, '__FINISH__,"finish"');
    assert.equal(api.state.translationProgress.retriedBatches, 1);
    assert.equal(api.state.translationProgress.updatedNodes, 0);
    assert.equal(api.state.translationProgress.skippedNodes, 1);
    assert.equal(api.state.translationProgress.finishedBatches, 1);
});

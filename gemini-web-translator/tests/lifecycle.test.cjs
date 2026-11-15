const assert = require('node:assert/strict');
const test = require('node:test');
const { load, textNode, prepare, options, sse } = require('./helpers.cjs');
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };

for (const synchronousCallback of [false, true]) {
    test(`cancel settles even when abort ${synchronousCallback ? 'reenters synchronously' : 'never calls back'}`, async () => {
        let callbacks; let aborts = 0;
        const { api } = load({}, value => { callbacks = value; return { abort() { aborts++; if (synchronousCallback) callbacks.onabort(); } }; });
        const { batches } = prepare(api);
        const pending = api.translateSingleBatch(options(batches[0]));
        const rejected = assert.rejects(pending, /取消/);
        [...api.state.activeRequests][0].abort();
        await rejected;
        assert.equal(aborts, 1);
        assert.equal(api.state.activeRequests.size, 0);
        callbacks.onload({ status: 200, responseText: sse('P_0_0,"late"\n__FINISH__,"finish"') });
        assert.equal(batches[0].completed, false);
    });
}

test('onload cannot finish or replay text while a readable stream owns the response', async () => {
    let callbacks;
    const first = deferred(); const eof = deferred(); const secondRead = deferred();
    let reads = 0; let released = 0;
    const { api } = load({}, value => { callbacks = value; return { abort() {} }; });
    const { nodes, batches } = prepare(api);
    const pending = api.translateSingleBatch(options(batches[0]));
    callbacks.onloadstart({ status: 200, response: { getReader: () => ({
        read() { if (++reads === 1) return first.promise; secondRead.resolve(); return eof.promise; },
        releaseLock() { released++; }
    }) } });
    callbacks.onload({ status: 200, responseText: sse('P_0_0,"wrong replay"\n__FINISH__,"finish"') });
    await Promise.resolve();
    assert.equal(api.state.activeRequests.size, 1);
    assert.equal(batches[0].completed, false);
    assert.equal(nodes[0].nodeValue, 'Hello');
    first.resolve({ done: false, value: Buffer.from(sse('P_0_0,"正确"\n__FINISH__,"finish"')) });
    await secondRead.promise;
    assert.equal(nodes[0].nodeValue, '正确');
    assert.equal(batches[0].completed, false);
    eof.resolve({ done: true });
    await pending;
    assert.equal(batches[0].completed, true);
    assert.equal(released, 1);
    assert.equal(api.state.translationProgress.finishedBatches, 1);
});

test('a failed reader cannot apply late data to a retry in the same run', async () => {
    const callbacks = []; let aborts = 0; let cancelled = 0;
    const delayed = deferred(); const released = deferred();
    const { api } = load({}, value => { callbacks.push(value); return { abort() { aborts++; } }; });
    const { nodes, batches } = prepare(api);
    const first = api.translateSingleBatch(options(batches[0]));
    callbacks[0].onloadstart({ status: 200, response: { getReader: () => ({
        read: () => delayed.promise, cancel() { cancelled++; }, releaseLock() { released.resolve(); }
    }) } });
    const rejected = assert.rejects(first, /offline/);
    callbacks[0].onerror({ error: 'offline' });
    await rejected;
    assert.equal(aborts, 1); assert.equal(cancelled, 1);
    api.resetBatchForRetry(batches[0]);
    const second = api.translateSingleBatch(options(batches[0]));
    delayed.resolve({ done: false, value: Buffer.from(sse('P_0_0,"stale"\n__FINISH__,"finish"')) });
    await released.promise;
    assert.equal(nodes[0].nodeValue, 'Hello');
    callbacks[1].onload({ status: 200, responseText: sse('P_0_0,"fresh"\n__FINISH__,"finish"') });
    await second;
    assert.equal(nodes[0].nodeValue, 'fresh');
    assert.equal(api.state.activeRequests.size, 0);
});

test('synchronous failure before handle return still aborts once and retires ownership', async () => {
    let aborts = 0;
    const { api } = load({}, callbacks => { callbacks.onerror({ error: 'test-key rejected' }); return { abort() { aborts++; } }; });
    const { batches } = prepare(api);
    await assert.rejects(api.translateSingleBatch(options(batches[0])), error => {
        assert.equal(error.message, '[redacted] rejected'); return true;
    });
    assert.equal(aborts, 1); assert.equal(api.state.activeRequests.size, 0);
});

test('permanent HTTP failures stop retries and unsent batches', async () => {
    let requests = 0;
    const { api } = load({}, callbacks => {
        requests++;
        queueMicrotask(() => callbacks.onload({ status: 401, responseText: '{"error":{"message":"Unauthorized"}}' }));
        return { abort() {} };
    });
    const { batches } = prepare(api, [textNode('One'), textNode('Two'), textNode('Three')]);
    await assert.rejects(api.translateAllBatches({ ...options(), batches }), /Unauthorized/);
    assert.equal(requests, 1); assert.equal(api.state.translationProgress.retriedBatches, 0);
});

test('Retry-After seconds/dates and error classification retain provider semantics', () => {
    const now = Date.UTC(2026, 10, 15, 6, 0, 0);
    class ClockDate extends Date { static now() { return now; } }
    const { api } = load({}, undefined, { Date: ClockDate });
    assert.equal(api.getHttpError({ status: 429, responseHeaders: 'Retry-After: 12\r\n' }).retryAfterMs, 12000);
    assert.ok(api.getRetryDelay(api.getHttpError({ status: 429, responseHeaders: 'retry-after: 12' }), 1) >= 12000);
    const later = new Date(now + 60000).toUTCString();
    const delay = api.getHttpError({ status: 503, responseHeaders: `Retry-After: ${later}` }).retryAfterMs;
    assert.equal(delay, 60000);
    for (const status of [400, 401, 403, 404, 422]) assert.equal(api.getHttpError({ status }).retryable, false);
    for (const status of [408, 409, 429, 500, 503]) assert.equal(api.getHttpError({ status }).retryable, true);
    assert.equal(api.getHttpError({ status: 429, responseHeaders: 'retry-after: invalid' }).retryAfterMs, 0);
});

test('retry waiting can be cancelled without waiting for a server delay', async () => {
    let scheduled; let cleared = false;
    const { api } = load({}, undefined, { setTimeout: (callback, delay) => { scheduled = delay; return 7; }, clearTimeout: id => { if (id === 7) cleared = true; } });
    prepare(api);
    const pending = api.waitForRetry(1, 120000);
    assert.ok(scheduled > 119000);
    [...api.state.retryWaits][0]();
    assert.equal(await pending, false); assert.equal(cleared, true); assert.equal(api.state.retryWaits.size, 0);
});

test('all batches use one request settings snapshot despite concurrent settings changes', async () => {
    const requests = [];
    const { api, storage } = load({ gm_openai_protocol: 'chat' }, callbacks => {
        const body = JSON.parse(callbacks.data); requests.push({ url: callbacks.url, body });
        storage.set('gm_openai_protocol', 'responses');
        storage.set('gm_openai_base_url', 'https://another.example/v1');
        queueMicrotask(() => callbacks.onload({ status: 200, responseText: sse('__FINISH__,"finish"') }));
        return { abort() {} };
    });
    const { batches } = prepare(api, [textNode('One'), textNode('Two')]);
    await api.translateAllBatches({ ...options(), batches });
    assert.equal(requests.length, 2);
    for (const request of requests) {
        assert.equal(request.url, 'https://api.openai.com/v1/chat/completions');
        assert.ok(request.body.messages); assert.equal(request.body.input, undefined);
    }
});

test('successful batches remain reusable when a later batch fails', async () => {
    let count = 0;
    const { api } = load({}, callbacks => {
        const first = ++count === 1;
        queueMicrotask(() => callbacks.onload(first
            ? { status: 200, responseText: sse('P_0_0,"成功"\n__FINISH__,"finish"') }
            : { status: 403, responseText: '{"error":{"message":"Forbidden"}}' }));
        return { abort() {} };
    });
    const { nodes, batches } = prepare(api, [textNode('One'), textNode('Two')]);
    const records = new Map();
    await assert.rejects(api.translateAllBatches({ ...options(), batches, onBatchComplete: batch => api.cacheCompletedBatch(batch, records) }), /Forbidden/);
    assert.equal(records.size, 1);
    api.restoreOriginalText();
    const entries = api.generateTranslationEntries(nodes);
    const result = api.applyCachedTranslations(entries, records);
    assert.equal(nodes[0].nodeValue, '成功'); assert.equal(nodes[1].nodeValue, 'Two');
    assert.equal(result.pending.length, 1); assert.equal(result.pending[0].id, entries[1].id);
});

test('truncated UTF-8 at readable EOF is a failure, not a successful replacement character', async () => {
    let callbacks;
    const { api } = load({}, value => { callbacks = value; return { abort() {} }; });
    const { batches } = prepare(api);
    const pending = api.translateSingleBatch(options(batches[0]));
    let read = false;
    callbacks.onloadstart({ status: 200, response: { getReader: () => ({
        read: async () => read ? { done: true } : (read = true, { done: false, value: Buffer.from([0xe4]) }),
        releaseLock() {}
    }) } });
    await assert.rejects(pending, /encoded data|encoding|UTF/i);
    assert.equal(batches[0].completed, false);
});

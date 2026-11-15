const assert = require('node:assert/strict');
const test = require('node:test');
const { load, textNode, prepare, options, sse } = require('./helpers.cjs');
const turn = () => new Promise(resolve => setImmediate(resolve));

for (const reason of [undefined, null, false, 0, '', { message: 123 }]) {
    test(`arbitrary readable error ${JSON.stringify(reason)} rejects and cannot become a completed batch`, { timeout: 3000 }, async () => {
        const { api } = load({}, callbacks => {
            queueMicrotask(() => callbacks.onloadstart({ status: 200, response: { getReader: () => ({
                read: () => Promise.reject(reason), releaseLock() {}
            }) } }));
            return { abort() {} };
        });
        const { batches } = prepare(api);
        await assert.rejects(api.translateSingleBatch(options(batches[0])), error => typeof error.message === 'string');
        assert.equal(batches[0].completed, false);
        assert.equal(api.state.activeRequests.size, 0);
    });
}

test('workers recheck a shared cooldown extended while they were already waiting', async () => {
    let now = 0;
    let nextId = 0;
    const timers = new Map();
    class ClockDate extends Date { static now() { return now; } }
    const globals = {
        Date: ClockDate, Math: Object.assign(Object.create(Math), { random: () => 0 }),
        setTimeout(callback, delay) { const id = ++nextId; timers.set(id, { callback, due: now + delay }); return id; },
        clearTimeout(id) { timers.delete(id); }
    };
    async function advance(milliseconds) {
        now += milliseconds;
        const ready = [...timers].filter(([, timer]) => timer.due <= now).sort((a, b) => a[1].due - b[1].due);
        for (const [id, timer] of ready) if (timers.delete(id)) timer.callback();
        await turn();
    }
    const callbacks = [];
    const { api } = load({}, callback => { callbacks.push(callback); return { abort() {} }; }, globals);
    const { batches } = prepare(api, ['A', 'B', 'C', 'D'].map(value => textNode(value)), 1, 3);
    const running = api.translateAllBatches({ ...options(), batches });
    try {
        await turn();
        assert.equal(callbacks.length, 3);
        callbacks[0].onload({ status: 429, responseHeaders: 'Retry-After: 1' });
        await turn();
        callbacks[1].onload({ status: 200, responseText: sse('__FINISH__,"finish"') });
        await turn();
        await advance(500);
        callbacks[2].onload({ status: 429, responseHeaders: 'Retry-After: 10' });
        await turn();
        await advance(500);
        assert.equal(callbacks.length, 3, 'the original one-second wait must not issue a request during the extended cooldown');
        await advance(9500);
        assert.ok(callbacks.length > 3, 'requests resume at the latest shared deadline');
    } finally {
        api.state.activeTranslationRunId++;
        for (const request of api.state.activeRequests) request.abort();
        for (const cancel of api.state.retryWaits) cancel();
        await running;
    }
});

const assert = require('node:assert/strict');
const test = require('node:test');
const { load } = require('./helpers.cjs');

function fixture(overrides = {}) {
    const created = [];
    function element(tag) {
        const node = { tag, style: {}, children: [], textContent: '', value: '', classList: { add() {}, remove() {} },
            append(...children) { this.children.push(...children); },
            appendChild(child) { this.children.push(child); child.parentNode = this; },
            replaceChildren(...children) { this.children = children; }
        };
        created.push(node);
        return node;
    }
    const elements = new Map(['gm-translator-container', 'gm-key-status', 'gm-platform', 'gm-openai-base-url'].map(id => [id, element('div')]));
    elements.get('gm-platform').value = 'gemini';
    const listeners = new Map();
    const globals = {
        document: { createElement: element, createDocumentFragment: () => element('fragment'),
            getElementById: id => elements.get(id), addEventListener: (name, callback) => listeners.set(name, callback) },
        requestAnimationFrame: callback => callback(), setTimeout: () => 1,
        window: { location: { origin: 'https://example.test' }, prompt: () => null, confirm: () => false },
        ...overrides
    };
    return { globals, created, elements, listeners };
}

for (const sandboxMode of ['dom', 'js']) {
    test(`${sandboxMode}: menu key editing uses an empty native prompt and never copies the key to DOM`, () => {
        const f = fixture({ GM_info: { sandboxMode } });
        let promptDefault;
        f.globals.window.prompt = (_, value) => { promptDefault = value; return 'new-private-test-key'; };
        const { api, storage, menus } = load({ gm_api_key_gemini: 'previous-private-test-key' }, undefined, f.globals);
        api.state.isUIInitialized = true;
        api.init();
        const configure = [...menus].find(([label]) => label.includes('Google Gemini 密钥'))[1];
        configure();
        assert.equal(promptDefault, '');
        assert.equal(storage.get('gm_api_key_gemini'), 'new-private-test-key');
        assert.equal(f.elements.get('gm-key-status').textContent, '已配置');
        for (const node of f.created) {
            assert.ok(!String(node.textContent).includes('private-test-key'));
            assert.ok(!String(node.value).includes('private-test-key'));
        }
    });
}

test('cancel and deletion confirmation preserve or remove only the selected credential', () => {
    const f = fixture();
    const { api, storage } = load({ gm_api_key_gemini: 'gemini-test', gm_api_key_openai: 'openai-test' }, undefined, f.globals);
    api.state.isUIInitialized = true;
    api.configureApiKey('gemini');
    assert.equal(storage.get('gm_api_key_gemini'), 'gemini-test');
    f.globals.window.prompt = () => '';
    api.configureApiKey('gemini');
    assert.equal(storage.get('gm_api_key_gemini'), 'gemini-test');
    f.globals.window.confirm = () => true;
    api.configureApiKey('gemini');
    assert.equal(storage.has('gm_api_key_gemini'), false);
    assert.equal(storage.get('gm_api_key_openai'), 'openai-test');
});

for (const sandboxMode of ['raw', 'unknown', undefined]) {
    test(`unsupported sandbox ${sandboxMode} does not read a key or open a credential prompt`, async () => {
        let reads = 0; let prompts = 0;
        const f = fixture({ GM_info: { sandboxMode }, GM_getValue: key => { if (key.startsWith('gm_api_key')) reads++; return ''; } });
        f.globals.window.prompt = () => { prompts++; return 'test-key'; };
        const { api, storage } = load({}, undefined, f.globals);
        api.state.isUIInitialized = true;
        api.configureApiKey('gemini');
        await api.startTranslationProcess();
        assert.equal(reads, 0); assert.equal(prompts, 0); assert.equal(storage.size, 0);
        assert.equal(api.requireIsolatedRuntime(), false);
    });
}

test('endpoint change confirms a captured native-dialog URL, never a page input', () => {
    const f = fixture();
    f.globals.window.prompt = () => 'https://gateway.example/v1/chat/completions';
    f.globals.window.confirm = message => {
        assert.ok(message.includes('https://gateway.example/v1'));
        f.elements.get('gm-openai-base-url').value = 'https://attacker.invalid/v1';
        return true;
    };
    const { api, storage } = load({}, undefined, f.globals);
    api.state.isUIInitialized = true;
    api.configureOpenAIBaseUrl();
    assert.equal(storage.get('gm_openai_base_url'), 'https://gateway.example/v1');
    f.globals.window.confirm = () => false;
    f.globals.window.prompt = () => 'https://other.example/v1';
    api.configureOpenAIBaseUrl();
    assert.equal(storage.get('gm_openai_base_url'), 'https://gateway.example/v1');
});

test('page-generated keyboard events cannot trigger translation or settings', () => {
    const f = fixture();
    const { api } = load({}, () => assert.fail('Synthetic input must not send requests'), f.globals);
    api.init();
    for (const key of ['t', 's']) f.listeners.get('keydown')({ isTrusted: false, altKey: true, key, preventDefault() {} });
    assert.equal(api.state.isUIInitialized, false);
    assert.equal(api.state.isTranslating, false);
});

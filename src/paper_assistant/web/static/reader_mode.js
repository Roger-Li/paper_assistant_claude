(function() {
    const STORAGE_KEYS = {
        enabled: 'paper-assistant.reader-mode.enabled',
        voice: 'paper-assistant.reader-mode.voice',
        rate: 'paper-assistant.reader-mode.rate',
    };

    const RATE_DEFAULT = '1';
    const VOICE_WAIT_MS = 1500;
    const MAX_UTTERANCE_CHARS = 900;
    const READY_STATUS =
        'Click any sentence to read from here. Press K or Space to pause or resume, and Escape to stop. Tables, equations, and code stay visible but are not read aloud.';
    const NOVELTY_VOICE_KEYWORDS = [
        'bad news',
        'bells',
        'boing',
        'bubbles',
        'cellos',
        'deranged',
        'good news',
        'hysterical',
        'pipe organ',
        'princess',
        'singing',
        'trinoids',
        'whisper',
        'wobble',
        'zarvox',
    ];
    const LOW_QUALITY_VOICE_KEYWORDS = ['compact', 'espeak', 'festival', 'pico'];
    const SPEAKABLE_SELECTOR = 'p, li, blockquote, h1, h2, h3';
    const PASSIVE_SELECTOR = 'pre, table, .katex-display';

    function safeLocalStorageGet(key) {
        try {
            return window.localStorage.getItem(key);
        } catch (_err) {
            return null;
        }
    }

    function safeLocalStorageSet(key, value) {
        try {
            window.localStorage.setItem(key, value);
        } catch (_err) {
            // Ignore storage failures in private browsing or restricted contexts.
        }
    }

    function normalizeWhitespace(text) {
        return text.replace(/\s+/g, ' ').trim();
    }

    function isHidden(element, rootElement) {
        // Ignore the reader root itself, because the cloned content starts hidden until the user enables Reader Mode.
        let current = element;
        while (current && current !== rootElement) {
            if (current.hasAttribute && (current.hasAttribute('hidden') || current.getAttribute('aria-hidden') === 'true')) {
                return true;
            }
            current = current.parentElement;
        }
        return false;
    }

    function isLikelyNoveltyVoice(voice) {
        const haystack = `${voice.name || ''} ${voice.voiceURI || ''}`.toLowerCase();
        return NOVELTY_VOICE_KEYWORDS.some((keyword) => haystack.includes(keyword));
    }

    function scoreVoice(voice) {
        const lang = (voice.lang || '').toLowerCase();
        const haystack = `${voice.name || ''} ${voice.voiceURI || ''}`.toLowerCase();
        let score = 0;

        if (voice.default) {
            score += 1000;
        }
        if (lang.startsWith('en-us')) {
            score += 220;
        } else if (lang.startsWith('en')) {
            score += 180;
        }
        if (voice.localService) {
            score += 120;
        }
        if (/\b(natural|neural|premium|enhanced)\b/i.test(voice.name || '')) {
            score += 70;
        }
        if (LOW_QUALITY_VOICE_KEYWORDS.some((keyword) => haystack.includes(keyword))) {
            score -= 150;
        }

        return score;
    }

    function sortVoices(voices) {
        return voices.slice().sort((left, right) => {
            const scoreDelta = scoreVoice(right) - scoreVoice(left);
            if (scoreDelta !== 0) {
                return scoreDelta;
            }
            return (left.name || '').localeCompare(right.name || '');
        });
    }

    function segmentSentencesWithOffsets(text) {
        if (!text) {
            return [];
        }

        if (window.Intl && typeof window.Intl.Segmenter === 'function') {
            const segmenter = new window.Intl.Segmenter(undefined, { granularity: 'sentence' });
            return Array.from(segmenter.segment(text))
                .map((part) => ({
                    rawStart: part.index,
                    rawEnd: part.index + part.segment.length,
                    text: normalizeWhitespace(part.segment),
                }))
                .filter((part) => part.text);
        }

        const pattern = /[^.!?]+(?:[.!?]+|$)/g;
        const segments = [];
        let match = pattern.exec(text);
        while (match) {
            const value = normalizeWhitespace(match[0]);
            if (value) {
                segments.push({
                    rawStart: match.index,
                    rawEnd: match.index + match[0].length,
                    text: value,
                });
            }
            match = pattern.exec(text);
        }
        return segments;
    }

    function scrollIntoViewIfNeeded(element) {
        const rect = element.getBoundingClientRect();
        const outsideViewport = rect.top < 96 || rect.bottom > window.innerHeight - 48;
        if (outsideViewport) {
            element.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    }

    function isNodeExcludedFromSpeech(node, blockElement) {
        const parent = node.parentElement;
        if (!parent) {
            return true;
        }
        if (parent.closest('pre, code, table, .katex, .katex-display')) {
            return true;
        }

        const blockTag = blockElement.tagName.toLowerCase();
        if (blockTag === 'li') {
            const closestLi = parent.closest('li');
            if (closestLi && closestLi !== blockElement) {
                return true;
            }
        }
        if (blockTag === 'blockquote') {
            const closestQuote = parent.closest('blockquote');
            if (closestQuote && closestQuote !== blockElement) {
                return true;
            }
        }

        return false;
    }

    function collectSpeakableTextNodes(blockElement) {
        const walker = document.createTreeWalker(
            blockElement,
            window.NodeFilter.SHOW_TEXT,
            {
                acceptNode(node) {
                    if (!node.textContent || !normalizeWhitespace(node.textContent)) {
                        return window.NodeFilter.FILTER_REJECT;
                    }
                    if (isNodeExcludedFromSpeech(node, blockElement)) {
                        return window.NodeFilter.FILTER_REJECT;
                    }
                    return window.NodeFilter.FILTER_ACCEPT;
                },
            }
        );

        const nodes = [];
        let currentNode = walker.nextNode();
        while (currentNode) {
            nodes.push(currentNode);
            currentNode = walker.nextNode();
        }
        return nodes;
    }

    function buildSentencePlanForBlock(blockElement, blockIndex, nextSentenceIndex) {
        const textNodes = collectSpeakableTextNodes(blockElement);
        if (!textNodes.length) {
            return { sentences: [], nextSentenceIndex };
        }

        const textNodeRanges = [];
        let rawText = '';
        textNodes.forEach((node) => {
            const start = rawText.length;
            rawText += node.textContent || '';
            textNodeRanges.push({
                node,
                start,
                end: rawText.length,
            });
        });

        const segments = segmentSentencesWithOffsets(rawText);
        if (!segments.length) {
            return { sentences: [], nextSentenceIndex };
        }

        const sentences = [];
        const operationsByNode = new Map();

        segments.forEach((segment, sentenceIndexInBlock) => {
            const sentenceMeta = {
                index: nextSentenceIndex + sentences.length,
                blockIndex,
                sentenceIndexInBlock,
                text: segment.text,
                fragments: [],
            };
            sentences.push(sentenceMeta);

            textNodeRanges.forEach(({ node, start, end }) => {
                const overlapStart = Math.max(segment.rawStart, start);
                const overlapEnd = Math.min(segment.rawEnd, end);
                if (overlapStart >= overlapEnd) {
                    return;
                }

                const relativeStart = overlapStart - start;
                const relativeEnd = overlapEnd - start;
                const existingOperations = operationsByNode.get(node) || [];
                existingOperations.push({
                    start: relativeStart,
                    end: relativeEnd,
                    sentenceIndex: sentenceMeta.index,
                });
                operationsByNode.set(node, existingOperations);
            });
        });

        operationsByNode.forEach((operations, node) => {
            operations.sort((left, right) => right.start - left.start || right.end - left.end);
            let workingNode = node;

            operations.forEach((operation) => {
                if (!workingNode.parentNode) {
                    return;
                }

                if (operation.end < workingNode.textContent.length) {
                    workingNode.splitText(operation.end);
                }

                let middleNode = workingNode;
                if (operation.start > 0) {
                    middleNode = workingNode.splitText(operation.start);
                }

                if (!middleNode.textContent) {
                    return;
                }

                const fragment = document.createElement('span');
                fragment.className = 'reader-sentence-fragment';
                fragment.dataset.readerIndex = String(operation.sentenceIndex);
                middleNode.parentNode.replaceChild(fragment, middleNode);
                fragment.appendChild(middleNode);
            });
        });

        return {
            sentences,
            nextSentenceIndex: nextSentenceIndex + sentences.length,
        };
    }

    function buildUtterancePlan(blocks, startSentence, maxChars) {
        if (!startSentence) {
            return [];
        }

        const chunks = [];
        let currentChunk = null;

        function flushChunk() {
            if (!currentChunk || !currentChunk.sentences.length) {
                currentChunk = null;
                return;
            }

            currentChunk.firstSentenceIndex = currentChunk.sentences[0].globalIndex;
            currentChunk.lastSentenceIndex =
                currentChunk.sentences[currentChunk.sentences.length - 1].globalIndex;
            chunks.push(currentChunk);
            currentChunk = null;
        }

        for (let blockIndex = startSentence.blockIndex; blockIndex < blocks.length; blockIndex += 1) {
            const block = blocks[blockIndex];
            const startOffset =
                blockIndex === startSentence.blockIndex ? startSentence.sentenceIndexInBlock : 0;
            const blockSentences = block.sentences.slice(startOffset);
            if (!blockSentences.length) {
                continue;
            }

            const blockText = blockSentences.map((sentence) => sentence.text).join(' ');
            if (!currentChunk) {
                currentChunk = { text: '', sentences: [] };
            }

            const pendingSeparator = currentChunk.sentences.length ? '\n\n' : '';
            const wouldOverflow =
                currentChunk.sentences.length
                && currentChunk.text.length + pendingSeparator.length + blockText.length > maxChars;
            if (wouldOverflow) {
                flushChunk();
                currentChunk = { text: '', sentences: [] };
            }

            const chunkSeparator = currentChunk.sentences.length ? '\n\n' : '';
            currentChunk.text += chunkSeparator;

            blockSentences.forEach((sentence, sentenceOffset) => {
                const prefix = sentenceOffset === 0 ? '' : ' ';
                const start = currentChunk.text.length + prefix.length;
                currentChunk.text += prefix + sentence.text;
                currentChunk.sentences.push({
                    globalIndex: sentence.index,
                    start,
                    text: sentence.text,
                });
            });
        }

        flushChunk();
        return chunks;
    }

    function findSentenceIndexForChar(sentences, charIndex) {
        for (let index = sentences.length - 1; index >= 0; index -= 1) {
            if (charIndex >= sentences[index].start) {
                return index;
            }
        }
        return 0;
    }

    function createReaderMode() {
        const state = {
            initialized: false,
            supported: typeof window !== 'undefined'
                && 'speechSynthesis' in window
                && 'SpeechSynthesisUtterance' in window,
            root: null,
            summary: null,
            content: null,
            status: null,
            enabledToggle: null,
            playButton: null,
            pauseButton: null,
            stopButton: null,
            voiceSelect: null,
            rateSelect: null,
            blocks: [],
            sentences: [],
            voices: [],
            currentIndex: null,
            paused: false,
            playbackToken: 0,
            voicesReady: false,
            voicesWaitTimer: null,
        };

        function setStatus(message, isError) {
            if (!state.status) {
                return;
            }
            state.status.textContent = message;
            state.status.classList.toggle('reader-status-error', !!isError);
        }

        function isTypingTarget(target) {
            return target instanceof window.Element
                && !!target.closest('input, textarea, select, [contenteditable], [contenteditable="true"], [contenteditable="plaintext-only"]');
        }

        function setSentenceClass(sentence, className, enabled) {
            if (!sentence) {
                return;
            }
            sentence.fragments.forEach((fragment) => {
                fragment.classList.toggle(className, enabled);
            });
        }

        function clearSentenceClasses() {
            state.sentences.forEach((sentence) => {
                setSentenceClass(sentence, 'is-active', false);
                setSentenceClass(sentence, 'is-paused', false);
                setSentenceClass(sentence, 'is-read', false);
            });
            state.currentIndex = null;
        }

        function clearActiveSentence() {
            state.sentences.forEach((sentence) => {
                setSentenceClass(sentence, 'is-active', false);
                setSentenceClass(sentence, 'is-paused', false);
            });
            state.currentIndex = null;
        }

        function markRead(index) {
            const sentence = state.sentences[index];
            if (!sentence) {
                return;
            }
            setSentenceClass(sentence, 'is-active', false);
            setSentenceClass(sentence, 'is-paused', false);
            setSentenceClass(sentence, 'is-read', true);
        }

        function markSentenceRange(startIndex, endIndex) {
            if (startIndex == null || endIndex == null) {
                return;
            }
            for (let index = startIndex; index <= endIndex; index += 1) {
                markRead(index);
            }
        }

        function setActiveSentence(index) {
            const sentence = state.sentences[index];
            if (!sentence) {
                return;
            }
            state.sentences.forEach((currentSentence, currentIndex) => {
                setSentenceClass(currentSentence, 'is-active', currentIndex === index);
                if (currentIndex !== index) {
                    setSentenceClass(currentSentence, 'is-paused', false);
                }
            });
            state.currentIndex = index;
            if (sentence.primaryFragment) {
                scrollIntoViewIfNeeded(sentence.primaryFragment);
            }
        }

        function advanceToSentence(index) {
            if (index == null || index === state.currentIndex) {
                return;
            }
            if (state.currentIndex != null && state.currentIndex < index) {
                markSentenceRange(state.currentIndex, index - 1);
            }
            setActiveSentence(index);
        }

        function setPaused(paused) {
            state.paused = paused;
            state.sentences.forEach((sentence, index) => {
                setSentenceClass(sentence, 'is-paused', paused && index === state.currentIndex);
            });
        }

        function isSpeaking() {
            return state.supported && (window.speechSynthesis.speaking || window.speechSynthesis.pending);
        }

        function isUnavailable() {
            return !state.supported || !state.sentences.length || !state.voicesReady;
        }

        function updateControls() {
            const modeEnabled = !!state.enabledToggle?.checked;
            const speaking = isSpeaking();
            const paused = state.paused || (state.supported && window.speechSynthesis.paused);
            const activePlayback = speaking || paused;
            const unavailable = isUnavailable();

            if (state.playButton) {
                state.playButton.disabled = unavailable;
            }
            if (state.pauseButton) {
                state.pauseButton.disabled = unavailable || !activePlayback;
                state.pauseButton.textContent = paused ? 'Resume' : 'Pause';
            }
            if (state.stopButton) {
                state.stopButton.disabled = unavailable || (!activePlayback && state.currentIndex == null);
            }
            if (state.voiceSelect) {
                state.voiceSelect.disabled = !state.supported || !state.voices.length;
            }
            if (state.rateSelect) {
                state.rateSelect.disabled = !state.supported;
            }
            if (state.content) {
                state.content.hidden = !modeEnabled;
            }
            if (state.summary) {
                state.summary.hidden = modeEnabled;
            }
            if (state.root) {
                state.root.classList.toggle('reader-mode-enabled', modeEnabled);
            }
        }

        function getPreferredVoice(voices) {
            const savedVoice = safeLocalStorageGet(STORAGE_KEYS.voice);
            if (savedVoice) {
                const matchedVoice = voices.find((voice) => voice.name === savedVoice);
                if (matchedVoice) {
                    return matchedVoice;
                }
            }

            const defaultVoice = voices.find((voice) => voice.default);
            if (defaultVoice) {
                return defaultVoice;
            }

            return voices[0] || null;
        }

        function getSelectedVoice() {
            if (!state.voices.length) {
                return null;
            }

            const selectedName = state.voiceSelect?.value;
            return state.voices.find((voice) => voice.name === selectedName) || getPreferredVoice(state.voices);
        }

        function populateVoices() {
            if (!state.supported || !state.voiceSelect) {
                return;
            }

            const rawVoices = window.speechSynthesis.getVoices().slice();
            const naturalVoices = rawVoices.filter((voice) => !isLikelyNoveltyVoice(voice));
            const voices = sortVoices(naturalVoices.length ? naturalVoices : rawVoices);

            state.voices = voices;
            state.voiceSelect.innerHTML = '';

            if (!voices.length) {
                state.voicesReady = false;
                updateControls();
                return;
            }

            const preferredVoice = getPreferredVoice(voices);
            voices.forEach((voice) => {
                const option = document.createElement('option');
                option.value = voice.name;
                option.textContent = [
                    voice.name,
                    voice.default ? 'Default' : '',
                    voice.lang || '',
                ].filter(Boolean).join(' · ');
                if (preferredVoice && voice.name === preferredVoice.name) {
                    option.selected = true;
                }
                state.voiceSelect.append(option);
            });

            state.voicesReady = true;
            safeLocalStorageSet(STORAGE_KEYS.voice, state.voiceSelect.value);
            if (state.voicesWaitTimer) {
                window.clearTimeout(state.voicesWaitTimer);
                state.voicesWaitTimer = null;
            }
            setReadyStatus();
            updateControls();
        }

        function stopPlayback(preserveReadState) {
            state.playbackToken += 1;
            if (state.supported) {
                window.speechSynthesis.cancel();
            }
            if (!preserveReadState) {
                clearSentenceClasses();
            } else {
                clearActiveSentence();
            }
            state.paused = false;
            updateControls();
        }

        function setReadyStatus() {
            setStatus(READY_STATUS);
        }

        function togglePauseResume() {
            if (!state.supported) {
                return false;
            }
            const canToggle = window.speechSynthesis.speaking
                || window.speechSynthesis.pending
                || window.speechSynthesis.paused;
            if (!canToggle) {
                return false;
            }
            if (window.speechSynthesis.paused) {
                window.speechSynthesis.resume();
            } else {
                window.speechSynthesis.pause();
            }
            updateControls();
            return true;
        }

        function stopWithStatus() {
            const hasPlayback =
                isSpeaking()
                || state.currentIndex != null
                || (state.supported && window.speechSynthesis.paused);
            if (!hasPlayback) {
                return false;
            }
            stopPlayback(true);
            setStatus('Stopped. Click any sentence to read from here.');
            return true;
        }

        function setReaderEnabled(enabled) {
            if (!state.enabledToggle) {
                return;
            }
            state.enabledToggle.checked = enabled;
            safeLocalStorageSet(STORAGE_KEYS.enabled, enabled ? '1' : '0');
            if (!enabled) {
                stopPlayback(true);
                setStatus('Reader mode is off. Turn it on to read prose in place while tables, equations, and code stay visible.');
            } else if (isUnavailable()) {
                if (!state.supported) {
                    setStatus('Browser speech is unavailable in this browser.', true);
                } else if (!state.voices.length) {
                    setStatus('Browser speech voices are unavailable. Reader mode requires desktop Brave or Chromium speech voices.', true);
                } else if (!state.sentences.length) {
                    setStatus('Reader mode could not find readable prose in this summary.', true);
                } else {
                    setStatus('Reader mode could not prepare this summary.', true);
                }
            } else {
                setReadyStatus();
            }
            updateControls();
        }

        function readFrom(startIndex) {
            if (isUnavailable()) {
                if (!state.supported) {
                    setStatus('Browser speech is unavailable in this browser.', true);
                } else if (!state.sentences.length) {
                    setStatus('Reader mode could not find readable prose in this summary.', true);
                } else if (!state.voices.length) {
                    setStatus('Browser speech voices are unavailable. Reader mode requires desktop Brave or Chromium speech voices.', true);
                } else {
                    setStatus('Reader mode is still loading browser voices. Please wait a moment and try again.', true);
                }
                updateControls();
                return;
            }

            if (!state.enabledToggle?.checked) {
                setReaderEnabled(true);
            }

            const startSentence = state.sentences[startIndex];
            const utterancePlan = buildUtterancePlan(state.blocks, startSentence, MAX_UTTERANCE_CHARS);
            if (!utterancePlan.length) {
                setStatus('Reader mode could not prepare this summary.', true);
                return;
            }

            stopPlayback(false);
            const token = state.playbackToken;
            const selectedVoice = getSelectedVoice();
            const rate = Number.parseFloat(state.rateSelect?.value || RATE_DEFAULT);

            utterancePlan.forEach((chunk, chunkIndex) => {
                const utterance = new window.SpeechSynthesisUtterance(chunk.text);
                utterance.rate = Number.isFinite(rate) ? rate : Number.parseFloat(RATE_DEFAULT);
                utterance.pitch = 1;
                utterance.volume = 1;

                if (selectedVoice) {
                    utterance.voice = selectedVoice;
                    utterance.lang = selectedVoice.lang;
                }

                utterance.onstart = () => {
                    if (token !== state.playbackToken) {
                        return;
                    }
                    setPaused(false);
                    setActiveSentence(chunk.firstSentenceIndex);
                    setStatus(`Reading sentence ${chunk.firstSentenceIndex + 1} of ${state.sentences.length}.`);
                    updateControls();
                };

                utterance.onboundary = (event) => {
                    if (token !== state.playbackToken || typeof event.charIndex !== 'number') {
                        return;
                    }
                    const sentenceOffset = findSentenceIndexForChar(chunk.sentences, event.charIndex);
                    const sentenceMeta = chunk.sentences[sentenceOffset];
                    if (!sentenceMeta || sentenceMeta.globalIndex === state.currentIndex) {
                        return;
                    }
                    advanceToSentence(sentenceMeta.globalIndex);
                    setStatus(`Reading sentence ${sentenceMeta.globalIndex + 1} of ${state.sentences.length}.`);
                    updateControls();
                };

                utterance.onpause = () => {
                    if (token !== state.playbackToken) {
                        return;
                    }
                    setPaused(true);
                    if (state.currentIndex != null) {
                        setStatus(`Paused on sentence ${state.currentIndex + 1}.`);
                    }
                    updateControls();
                };

                utterance.onresume = () => {
                    if (token !== state.playbackToken) {
                        return;
                    }
                    setPaused(false);
                    if (state.currentIndex != null) {
                        setStatus(`Reading sentence ${state.currentIndex + 1} of ${state.sentences.length}.`);
                    }
                    updateControls();
                };

                utterance.onerror = () => {
                    if (token !== state.playbackToken) {
                        return;
                    }
                    stopPlayback(true);
                    setStatus('Reader mode stopped because browser speech failed.', true);
                };

                utterance.onend = () => {
                    if (token !== state.playbackToken) {
                        return;
                    }
                    markSentenceRange(chunk.firstSentenceIndex, chunk.lastSentenceIndex);
                    setPaused(false);
                    if (chunkIndex === utterancePlan.length - 1) {
                        state.currentIndex = null;
                        setStatus('Finished reading. Click any sentence to read from here, or press K or Space next time to pause.');
                        updateControls();
                    }
                };

                window.speechSynthesis.speak(utterance);
            });

            updateControls();
        }

        function bindSentenceInteractions() {
            state.sentences.forEach((sentence, index) => {
                sentence.fragments.forEach((fragment) => {
                    fragment.addEventListener('click', (event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        readFrom(index);
                    });
                });

                if (!sentence.primaryFragment) {
                    return;
                }

                sentence.primaryFragment.setAttribute('role', 'button');
                sentence.primaryFragment.setAttribute('tabindex', '0');
                sentence.primaryFragment.setAttribute('aria-label', `Read from here: ${sentence.text}`);
                sentence.primaryFragment.addEventListener('keydown', (event) => {
                    if (event.key !== 'Enter' && event.key !== ' ') {
                        return;
                    }
                    event.preventDefault();
                    readFrom(index);
                });
            });
        }

        function buildReaderBlocks() {
            if (!state.summary || !state.content) {
                return;
            }

            state.content.innerHTML = state.summary.innerHTML;
            state.content.querySelectorAll(PASSIVE_SELECTOR).forEach((element) => {
                element.classList.add('reader-passive-block');
            });

            const blocks = [];
            const sentenceMetaByIndex = [];
            let nextSentenceIndex = 0;

            state.content.querySelectorAll(SPEAKABLE_SELECTOR).forEach((element) => {
                if (isHidden(element, state.content)) {
                    return;
                }

                if (element.tagName.toLowerCase() === 'p' && element.closest('li, blockquote')) {
                    return;
                }

                if (element.closest('pre, code, table, .katex, .katex-display')) {
                    return;
                }

                const blockIndex = blocks.length;
                const sentencePlan = buildSentencePlanForBlock(element, blockIndex, nextSentenceIndex);
                if (!sentencePlan.sentences.length) {
                    return;
                }

                nextSentenceIndex = sentencePlan.nextSentenceIndex;
                blocks.push({
                    blockIndex,
                    tagName: element.tagName.toLowerCase(),
                    sentences: sentencePlan.sentences,
                });

                sentencePlan.sentences.forEach((sentence) => {
                    sentenceMetaByIndex[sentence.index] = sentence;
                });
            });

            state.content.querySelectorAll('.reader-sentence-fragment').forEach((fragment) => {
                const sentenceIndex = Number.parseInt(fragment.dataset.readerIndex || '', 10);
                const sentence = sentenceMetaByIndex[sentenceIndex];
                if (!sentence) {
                    return;
                }
                sentence.fragments.push(fragment);
                if (!sentence.primaryFragment) {
                    sentence.primaryFragment = fragment;
                }
            });

            state.blocks = blocks;
            state.sentences = sentenceMetaByIndex.filter(Boolean);
            bindSentenceInteractions();
        }

        function bindEvents() {
            state.enabledToggle?.addEventListener('change', (event) => {
                setReaderEnabled(event.target.checked);
            });

            state.playButton?.addEventListener('click', () => {
                if (!state.sentences.length) {
                    return;
                }
                readFrom(0);
            });

            state.pauseButton?.addEventListener('click', () => {
                togglePauseResume();
            });

            state.stopButton?.addEventListener('click', () => {
                stopWithStatus();
            });

            state.voiceSelect?.addEventListener('change', () => {
                safeLocalStorageSet(STORAGE_KEYS.voice, state.voiceSelect.value);
            });

            state.rateSelect?.addEventListener('change', () => {
                safeLocalStorageSet(STORAGE_KEYS.rate, state.rateSelect.value);
            });

            if (state.supported) {
                if (typeof window.speechSynthesis.addEventListener === 'function') {
                    window.speechSynthesis.addEventListener('voiceschanged', populateVoices);
                } else {
                    window.speechSynthesis.onvoiceschanged = populateVoices;
                }
                window.addEventListener('keydown', (event) => {
                    if (!state.enabledToggle?.checked || event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) {
                        return;
                    }
                    if (isTypingTarget(event.target)) {
                        return;
                    }

                    const activePlayback = isSpeaking() || window.speechSynthesis.paused;
                    const key = event.key.toLowerCase();
                    if (activePlayback && (key === 'k' || event.key === ' ' || event.code === 'Space')) {
                        if (togglePauseResume()) {
                            event.preventDefault();
                            event.stopPropagation();
                        }
                        return;
                    }
                    if (event.key === 'Escape' && stopWithStatus()) {
                        event.preventDefault();
                        event.stopPropagation();
                    }
                }, true);
                window.addEventListener('beforeunload', () => stopPlayback(true));
                window.addEventListener('pagehide', () => stopPlayback(true));
            }
        }

        function applyStoredPreferences() {
            const storedRate = safeLocalStorageGet(STORAGE_KEYS.rate);
            if (storedRate && state.rateSelect?.querySelector(`option[value="${storedRate}"]`)) {
                state.rateSelect.value = storedRate;
            } else if (state.rateSelect) {
                state.rateSelect.value = RATE_DEFAULT;
            }
        }

        function init(config) {
            if (state.initialized) {
                return;
            }

            state.root = document.getElementById(config.rootId);
            state.summary = document.getElementById(config.summaryId);

            if (!state.root || !state.summary) {
                return;
            }

            state.content = state.root.querySelector('#reader-mode-content');
            state.status = state.root.querySelector('#reader-status');
            state.enabledToggle = state.root.querySelector('#reader-mode-enabled');
            state.playButton = state.root.querySelector('#reader-play-top');
            state.pauseButton = state.root.querySelector('#reader-pause');
            state.stopButton = state.root.querySelector('#reader-stop');
            state.voiceSelect = state.root.querySelector('#reader-voice');
            state.rateSelect = state.root.querySelector('#reader-rate');

            buildReaderBlocks();
            applyStoredPreferences();
            bindEvents();

            if (!state.sentences.length) {
                setStatus('Reader mode could not find readable prose in this summary.', true);
                updateControls();
                state.initialized = true;
                return;
            }

            if (!state.supported) {
                setStatus('Browser speech is unavailable in this browser.', true);
                updateControls();
                state.initialized = true;
                return;
            }

            setStatus('Loading browser voices for Reader Mode...');
            populateVoices();
            state.voicesWaitTimer = window.setTimeout(() => {
                if (!state.voices.length) {
                    setStatus('Browser speech voices are unavailable. Reader mode requires desktop Brave or Chromium speech voices.', true);
                    updateControls();
                }
            }, VOICE_WAIT_MS);

            const storedEnabled = safeLocalStorageGet(STORAGE_KEYS.enabled) === '1';
            setReaderEnabled(storedEnabled);
            state.initialized = true;
        }

        return {
            init,
            stop() {
                stopPlayback(true);
            },
        };
    }

    window.PaperReaderMode = createReaderMode();
})();

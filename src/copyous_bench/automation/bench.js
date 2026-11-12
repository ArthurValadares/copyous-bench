// Copyous branch benchmark automation script for gnome-shell-test-tool.
//
// The test tool runs one fresh GNOME Shell per iteration and calls init()
// during shell startup and run() once startup has completed.  The wrapper
// script (bench/wrap/wrap.sh) writes bench-iter.json into the iteration's
// private XDG config directory; that file tells this script which branch,
// configuration, dataset size and sample index the current shell belongs to.
//
// Everything here only touches surfaces that exist on all five branches:
//   - extension.clipboardDialog
//   - dialog._dialog / dialog._header.searchEntry
//   - dialog._scrollView.hadjustment / _scrollContainer
//   - _scrollContainer.get_children() and the ClipboardItem style classes
//   - _scrollContainer._filtered when the branch has it
//
// Raw measurements are written to a sidecar JSON file per iteration.  A small
// set of headline values is also exposed through METRICS so the test tool's
// report carries a secondary copy.

import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';

const UUID = 'copyous@boerdereinar.dev';
const EXTENSION_STATE_ACTIVE = 1;
const EXTENSION_STATE_ERROR = 3;

const OPEN_ANIMATION_TIMEOUT_MS = 5000;
const DATA_LOAD_TIMEOUT_MS = 20000;
const SETTLE_IDLE_MS = 250;
const SETTLE_TIMEOUT_MS = 12000;

export const METRICS = {
	copyousError: { description: 'Benchmark script error', units: '', value: 0 },
	copyousStartupToRun: { description: 'Time from shell init to automation run', units: 'ms', value: 0 },
	copyousStartupReady: { description: 'Time from shell init until clipboard history is loaded', units: 'ms', value: 0 },
	copyousOpenFirstFrame: { description: 'Time from dialog.open() to first painted frame', units: 'ms', value: 0 },
	copyousOpenAnimation: { description: 'Time until dialog open animation completed', units: 'ms', value: 0 },
	copyousOpenSettle: { description: 'Time from dialog.open() until the stage is idle', units: 'ms', value: 0 },
	copyousScrollFirstFrame: { description: 'Time from scroll start to first painted frame', units: 'ms', value: 0 },
	copyousScrollSettle: { description: 'Time from scroll start until the stage is idle', units: 'ms', value: 0 },
	copyousScrollLongFrames: { description: 'Number of >50ms frame intervals during scroll', units: 'count', value: 0 },
	copyousSearchNoMatchApply: { description: 'Synchronous time to apply the no-match search', units: 'ms', value: 0 },
	copyousSearchMatchApply: { description: 'Synchronous time to apply the matching search', units: 'ms', value: 0 },
	copyousSearchClearApply: { description: 'Synchronous time to clear the search', units: 'ms', value: 0 },
	copyousSearchClearFirstFrame: { description: 'Time from clearing the search to first painted frame', units: 'ms', value: 0 },
	copyousSearchClearReady: { description: 'Time from clearing the search until all entries are results', units: 'ms', value: 0 },
	copyousRssReady: { description: 'Resident set size after history load', units: 'kB', value: 0 },
	copyousRssEnd: { description: 'Resident set size at the end of the run', units: 'kB', value: 0 },
	copyousCpuTotal: { description: 'Process CPU time used during the run', units: 'ms', value: 0 },
	copyousActorsReady: { description: 'Actor count after history load', units: 'count', value: 0 },
	copyousActorsEnd: { description: 'Actor count at the end of the run', units: 'count', value: 0 },
};

let tInitUs = 0;
let frames = [];
let frameSignalId = 0;

function nowUs() {
	return GLib.get_monotonic_time();
}

function msBetween(startUs, endUs) {
	return (endUs - startUs) / 1000;
}

function round(value, digits = 3) {
	if (value == null || !Number.isFinite(value)) return null;
	const factor = 10 ** digits;
	return Math.round(value * factor) / factor;
}

function readJson(path) {
	const [ok, contents] = GLib.file_get_contents(path);
	if (!ok) throw new Error(`Could not read ${path}`);
	return JSON.parse(new TextDecoder().decode(contents));
}

function writeJson(path, value) {
	const data = JSON.stringify(value, null, 2);
	if (!GLib.file_set_contents(path, data)) throw new Error(`Could not write ${path}`);
}

function readProcStatus() {
	const out = { rss_kb: null, hwm_kb: null, threads: null };
	try {
		const [ok, contents] = GLib.file_get_contents('/proc/self/status');
		if (!ok) return out;
		for (const line of new TextDecoder().decode(contents).split('\n')) {
			const match = /^(VmRSS|VmHWM|Threads):\s+(\d+)/.exec(line);
			if (!match) continue;
			if (match[1] === 'VmRSS') out.rss_kb = Number(match[2]);
			else if (match[1] === 'VmHWM') out.hwm_kb = Number(match[2]);
			else if (match[1] === 'Threads') out.threads = Number(match[2]);
		}
	} catch {
		// Ignore /proc failures; the fields stay null.
	}
	return out;
}

function readCpuMs() {
	try {
		const [ok, contents] = GLib.file_get_contents('/proc/self/stat');
		if (!ok) return null;
		const text = new TextDecoder().decode(contents);
		const end = text.lastIndexOf(')');
		if (end < 0) return null;
		// After the comm field, fields start at state (field 3).  utime and
		// stime are fields 14 and 15, i.e. indices 11 and 12 here.
		const fields = text.slice(end + 2).trim().split(/\s+/);
		const utime = Number(fields[11]);
		const stime = Number(fields[12]);
		if (!Number.isFinite(utime) || !Number.isFinite(stime)) return null;
		// Linux USER_HZ is 100 on x86_64.
		return (utime + stime) * 10;
	} catch {
		return null;
	}
}

function countActors(root) {
	let count = 0;
	const stack = [root];
	while (stack.length > 0) {
		const actor = stack.pop();
		count++;
		if (count > 200000) break;
		if (typeof actor.get_children !== 'function') continue;
		for (const child of actor.get_children()) stack.push(child);
	}
	return count;
}

function hasClass(actor, name) {
	return typeof actor.has_style_class_name === 'function' && actor.has_style_class_name(name);
}

function isStatusItem(actor) {
	return hasClass(actor, 'status-item');
}

function isPlaceholder(actor) {
	return hasClass(actor, 'clipboard-item-placeholder');
}

function isRealItem(actor) {
	return hasClass(actor, 'clipboard-item') && !isStatusItem(actor) && !isPlaceholder(actor);
}

function itemCounts(container) {
	const counts = { children: container.get_n_children(), items: 0, placeholders: 0, status: 0, other: 0 };
	for (const child of container.get_children()) {
		if (isStatusItem(child)) counts.status++;
		else if (isPlaceholder(child)) counts.placeholders++;
		else if (isRealItem(child)) counts.items++;
		else counts.other++;
	}
	return counts;
}

// Number of entries that match the current search.  The lazy and placeholder
// branches expose _filtered; the main branch keeps every widget around and
// hides the non-matching ones, so count visible real items there.
function resultCount(container) {
	if (container._filtered) return container._filtered.length;

	let count = 0;
	for (const child of container.get_children()) {
		if (isRealItem(child) && child.visible) count++;
	}
	return count;
}

// Number of entries known to the container, independent of search state.
function dataCount(container) {
	if (container._filtered) return container._filtered.length;
	return itemCounts(container).children - itemCounts(container).status;
}

function firstFrameAfter(tUs, startIndex) {
	for (let i = startIndex; i < frames.length; i++) {
		if (frames[i] >= tUs) return frames[i];
	}
	return null;
}

function relativeFrames(frameTimes, startUs, endUs) {
	const result = [];
	for (const timestamp of frameTimes) {
		if (timestamp < startUs || timestamp > endUs) continue;
		result.push(round(msBetween(startUs, timestamp)));
	}
	return result;
}

function frameSummary(frameTimes, startUs, endUs) {
	const relative = [];
	for (const timestamp of frameTimes) {
		if (timestamp < startUs || timestamp > endUs) continue;
		relative.push(msBetween(startUs, timestamp));
	}

	const intervals = [];
	for (let i = 1; i < relative.length; i++) intervals.push(relative[i] - relative[i - 1]);
	intervals.sort((a, b) => a - b);

	const count = intervals.length;
	const total = intervals.reduce((sum, value) => sum + value, 0);
	const pick = (fraction) => {
		if (count === 0) return null;
		const index = Math.min(count - 1, Math.max(0, Math.floor(fraction * (count - 1))));
		return intervals[index];
	};

	return {
		frames: relative.length,
		duration_ms: round(msBetween(startUs, endUs)),
		first_ms: relative.length > 0 ? round(relative[0]) : null,
		last_ms: relative.length > 0 ? round(relative[relative.length - 1]) : null,
		mean_interval_ms: count > 0 ? round(total / count) : null,
		median_interval_ms: round(pick(0.5)),
		p95_interval_ms: round(pick(0.95)),
		max_interval_ms: count > 0 ? round(intervals[count - 1]) : null,
		long_50: intervals.filter((value) => value > 50).length,
		long_100: intervals.filter((value) => value > 100).length,
		fps:
			relative.length > 1
				? round((relative.length - 1) / ((relative[relative.length - 1] - relative[0]) / 1000))
				: null,
	};
}

async function waitUntil(condition, timeoutMs) {
	const start = nowUs();
	while (msBetween(start, nowUs()) < timeoutMs) {
		if (condition()) return { ok: true, t: nowUs(), waited_ms: round(msBetween(start, nowUs())) };
		await Scripting.sleep(10);
	}
	return { ok: false, t: nowUs(), waited_ms: round(msBetween(start, nowUs())) };
}

async function settle(idleMs = SETTLE_IDLE_MS, timeoutMs = SETTLE_TIMEOUT_MS) {
	const start = nowUs();
	let lastFrameCount = frames.length;
	let lastChange = nowUs();

	while (msBetween(start, nowUs()) < timeoutMs) {
		await Scripting.sleep(16);
		if (frames.length !== lastFrameCount) {
			lastFrameCount = frames.length;
			lastChange = nowUs();
			continue;
		}
		if (msBetween(lastChange, nowUs()) >= idleMs)
			return { ok: true, waited_ms: round(msBetween(start, nowUs())) };
	}

	return { ok: false, waited_ms: round(msBetween(start, nowUs())) };
}

function checkpoint(name, container, includeActors = false) {
	const status = readProcStatus();
	const counts = container ? itemCounts(container) : null;
	return {
		name,
		t_ms: round(msBetween(tInitUs, nowUs())),
		rss_kb: status.rss_kb,
		hwm_kb: status.hwm_kb,
		threads: status.threads,
		cpu_ms: readCpuMs(),
		actors: includeActors ? countActors(global.stage) : null,
		children: counts ? counts.children : null,
		items: counts ? counts.items : null,
		placeholders: counts ? counts.placeholders : null,
		status_items: counts ? counts.status : null,
		result_count: container ? resultCount(container) : null,
		data_count: container ? dataCount(container) : null,
	};
}

async function waitForExtension(timeoutMs) {
	const start = nowUs();
	let lastState = null;
	let lastError = '';

	// While an extension is loading, GNOME Shell temporarily sets its state to
	// ERROR (see ExtensionSystem.loadExtension), so ERROR is only meaningful
	// once the timeout expires.
	while (msBetween(start, nowUs()) < timeoutMs) {
		const entry = Main.extensionManager.lookup(UUID);
		if (entry) {
			lastState = entry.state;
			lastError = entry.error ?? '';
			if (entry.state === EXTENSION_STATE_ACTIVE && entry.stateObj?.clipboardDialog) return entry.stateObj;
		}
		await Scripting.sleep(50);
	}
	throw new Error(`Timed out waiting for extension (last state ${lastState}, error: ${lastError})`);
}

async function waitForData(extension, expected, timeoutMs) {
	const container = extension.clipboardDialog._scrollView._scrollContainer;
	const start = nowUs();
	while (msBetween(start, nowUs()) < timeoutMs) {
		if (dataCount(container) >= expected) return { ok: true, waited_ms: round(msBetween(start, nowUs())) };
		await Scripting.sleep(25);
	}
	return { ok: false, waited_ms: round(msBetween(start, nowUs())) };
}

async function measureOpen(dialog) {
	const container = dialog._scrollView._scrollContainer;
	const startIndex = frames.length;
	const t0 = nowUs();

	dialog.open();
	const opened = Boolean(dialog.opened);
	const grabbed = Boolean(dialog._grab);

	const firstFrameWait = await waitUntil(() => firstFrameAfter(t0, startIndex) !== null, OPEN_ANIMATION_TIMEOUT_MS);
	const firstFrame = firstFrameAfter(t0, startIndex);
	const animation = await waitUntil(() => dialog._dialog.opacity >= 254, OPEN_ANIMATION_TIMEOUT_MS);
	const settleResult = await settle();
	const t1 = nowUs();
	const frameTimes = frames.slice(startIndex);

	return {
		opened,
		grabbed,
		first_frame_ms: firstFrame ? round(msBetween(t0, firstFrame)) : null,
		first_frame_ok: firstFrameWait.ok,
		animation_ms: animation.ok ? round(msBetween(t0, animation.t)) : null,
		animation_ok: animation.ok,
		settle_ms: round(msBetween(t0, t1)),
		settled: settleResult.ok,
		opacity: dialog._dialog.opacity,
		width: dialog._dialog.get_width(),
		height: dialog._dialog.get_height(),
		counts: itemCounts(container),
		result_count: resultCount(container),
		data_count: dataCount(container),
		frames_ms: relativeFrames(frameTimes, t0, t1),
		frame_summary: frameSummary(frameTimes, t0, t1),
	};
}

async function measureScroll(dialog) {
	const scrollView = dialog._scrollView;
	const container = scrollView._scrollContainer;
	const adjustment = scrollView.hadjustment;

	// Start from the beginning and let any pending work finish.
	adjustment.value = adjustment.lower;
	await settle();

	const startIndex = frames.length;
	const t0 = nowUs();
	const before = {
		value: round(adjustment.value),
		lower: round(adjustment.lower),
		upper: round(adjustment.upper),
		page_size: round(adjustment.page_size),
	};

	let target = Math.max(adjustment.lower, adjustment.upper - adjustment.page_size);
	adjustment.ease(target, { duration: 700, mode: Clutter.AnimationMode.EASE_OUT_QUAD });
	let passes = 1;

	const firstFrameWait = await waitUntil(() => firstFrameAfter(t0, startIndex) !== null, 5000);
	const firstFrame = firstFrameAfter(t0, startIndex);
	const reached = await waitUntil(() => Math.abs(adjustment.value - target) < 1.5, 6000);
	const settleResult = await settle();

	// Lazy branches may grow the adjustment while widgets are realized.  If the
	// end moved, make one more pass so the measured scroll reaches the real end.
	let extra = null;
	const finalTarget = Math.max(adjustment.lower, adjustment.upper - adjustment.page_size);
	if (finalTarget > target + 2) {
		target = finalTarget;
		adjustment.ease(target, { duration: 500, mode: Clutter.AnimationMode.EASE_OUT_QUAD });
		passes++;
		const reachedAgain = await waitUntil(() => Math.abs(adjustment.value - target) < 1.5, 6000);
		const settleAgain = await settle();
		extra = { reached: reachedAgain.ok, settled: settleAgain.ok, settle_ms: settleAgain.waited_ms };
	}

	const t1 = nowUs();
	const frameTimes = frames.slice(startIndex);

	return {
		passes,
		first_frame_ms: firstFrame ? round(msBetween(t0, firstFrame)) : null,
		first_frame_ok: firstFrameWait.ok,
		reached: reached.ok,
		settle_ms: round(msBetween(t0, t1)),
		settled: settleResult.ok,
		before,
		after: {
			value: round(adjustment.value),
			lower: round(adjustment.lower),
			upper: round(adjustment.upper),
			page_size: round(adjustment.page_size),
		},
		target: round(target),
		counts: itemCounts(container),
		result_count: resultCount(container),
		data_count: dataCount(container),
		extra,
		frames_ms: relativeFrames(frameTimes, t0, t1),
		frame_summary: frameSummary(frameTimes, t0, t1),
	};
}

async function measureSearch(dialog, context) {
	const scrollView = dialog._scrollView;
	const container = scrollView._scrollContainer;
	const searchEntry = dialog._header.searchEntry;
	const adjustment = scrollView.hadjustment;

	adjustment.value = adjustment.lower;
	await settle();

	const steps = [
		{ name: 'nomatch', text: context.no_match_token, expected: 0 },
		{ name: 'match', text: context.token, expected: context.token_matches },
		{ name: 'clear', text: '', expected: context.expected_entries },
	];

	const results = {};
	for (const step of steps) {
		const startIndex = frames.length;
		const t0 = nowUs();

		searchEntry.text = step.text;
		const applyMs = round(msBetween(t0, nowUs()));

		const firstFrameWait = await waitUntil(
			() => firstFrameAfter(t0, startIndex) !== null,
			5000,
		);
		const ready = await waitUntil(() => resultCount(container) === step.expected, 8000);
		const settleResult = await settle();
		const firstFrame = firstFrameAfter(t0, startIndex);
		const t1 = nowUs();
		const frameTimes = frames.slice(startIndex);

		results[step.name] = {
			text: step.text,
			expected: step.expected,
			apply_ms: applyMs,
			first_frame_ms: firstFrame ? round(msBetween(t0, firstFrame)) : null,
			first_frame_ok: firstFrameWait.ok,
			ready_ms: ready.ok ? round(msBetween(t0, ready.t)) : null,
			ready_ok: ready.ok,
			settle_ms: round(msBetween(t0, t1)),
			settled: settleResult.ok,
			result_count: resultCount(container),
			counts: itemCounts(container),
			data_count: dataCount(container),
			adjustment: {
				value: round(adjustment.value),
				upper: round(adjustment.upper),
				page_size: round(adjustment.page_size),
			},
			frames_ms: relativeFrames(frameTimes, t0, t1),
			frame_summary: frameSummary(frameTimes, t0, t1),
		};

		log(
			`BENCH search ${step.name}: apply=${results[step.name].apply_ms}ms ` +
				`ready=${results[step.name].ready_ms}ms results=${results[step.name].result_count}`,
		);
	}

	return results;
}

function emptySidecar(context, error) {
	return {
		schema: 1,
		run_id: context?.run_id ?? null,
		campaign_id: context?.campaign_id ?? null,
		branch: context?.branch ?? null,
		commit: context?.commit ?? null,
		config: context?.config ?? null,
		size: context?.size ?? null,
		iteration: context?.iteration ?? null,
		warmup: context?.warmup ?? null,
		started_at: GLib.DateTime.new_now_utc().format('%Y-%m-%dT%H:%M:%SZ'),
		error: error == null ? null : String(error.stack ?? error),
	};
}

export function init() {
	tInitUs = nowUs();
	frameSignalId = global.stage.connect('after-paint', () => {
		frames.push(nowUs());
	});
	log(`BENCH init after-paint connected (${frameSignalId})`);
}

export async function run() {
	let context = null;
	try {
		// The perf helper establishes its idle timeout before GApplication
		// parses --idle-timeout, so the long timeout configured through
		// GNOME_SHELL_BUILDDIR only applies after the first D-Bus call.  Make
		// one harmless call now, otherwise the helper exits after the 30s
		// default in the middle of a benchmark and the shell cannot write its
		// final report.
		try {
			await Scripting.destroyTestWindows();
			log('BENCH perf helper timeout refreshed');
		} catch (error) {
			log(`BENCH could not refresh perf helper: ${error}`);
		}

		const contextPath = GLib.build_filenamev([GLib.get_user_config_dir(), 'bench-iter.json']);
		context = readJson(contextPath);
	} catch (error) {
		log(`BENCH could not read iteration context: ${error}`);
		return;
	}

	const sidecarPath = context.sidecar_path;
	const tRun = nowUs();
	let phase = 'startup';
	log(`BENCH run start ${context.run_id} iteration=${context.iteration} config=${context.config} size=${context.size}`);

	try {
		const extension = await waitForExtension(DATA_LOAD_TIMEOUT_MS);
		log(`BENCH extension ready after ${round(msBetween(tRun, nowUs()))}ms`);
		const container = extension.clipboardDialog._scrollView._scrollContainer;

		// The search entry gets focus when the dialog opens.  Disable the
		// blinking cursor so idle detection is not disturbed by its repaints.
		try {
			const searchEntry = extension.clipboardDialog._header.searchEntry;
			searchEntry.clutter_text.cursor_visible = false;
			searchEntry.clutter_text.cursor_blink = false;
		} catch {
			// Not fatal on any branch.
		}

		const dataWait = await waitForData(extension, context.expected_entries, DATA_LOAD_TIMEOUT_MS);
		const tReady = nowUs();

		const startup = {
			init_to_run_ms: round(msBetween(tInitUs, tRun)),
			ready_ms: round(msBetween(tInitUs, tReady)),
			data_wait_ms: dataWait.waited_ms,
			data_ok: dataWait.ok,
			data_count: dataCount(container),
			result_count: resultCount(container),
			counts: itemCounts(container),
			frame_summary: frameSummary(frames.slice(0), tInitUs, tReady),
			checkpoint: checkpoint('ready', container, true),
		};

		METRICS.copyousStartupToRun.value = startup.init_to_run_ms ?? 0;
		METRICS.copyousStartupReady.value = startup.ready_ms ?? 0;
		METRICS.copyousRssReady.value = startup.checkpoint.rss_kb ?? 0;
		METRICS.copyousActorsReady.value = startup.checkpoint.actors ?? 0;

		if (!dataWait.ok) throw new Error(`Only ${dataCount(container)} of ${context.expected_entries} entries loaded`);

		const checkpoints = [startup.checkpoint];
		const phases = { startup };

		phase = 'open';
		const open = await measureOpen(extension.clipboardDialog);
		phases.open = open;
		checkpoints.push(checkpoint('after_open', container));

		METRICS.copyousOpenFirstFrame.value = open.first_frame_ms ?? 0;
		METRICS.copyousOpenAnimation.value = open.animation_ms ?? 0;
		METRICS.copyousOpenSettle.value = open.settle_ms ?? 0;

		if (context.config === 'scroll' || context.config === 'all') {
			phase = 'scroll';
			const scroll = await measureScroll(extension.clipboardDialog);
			phases.scroll = scroll;
			checkpoints.push(checkpoint('after_scroll', container));

			METRICS.copyousScrollFirstFrame.value = scroll.first_frame_ms ?? 0;
			METRICS.copyousScrollSettle.value = scroll.settle_ms ?? 0;
			METRICS.copyousScrollLongFrames.value = scroll.frame_summary.long_50 ?? 0;
		}

		if (context.config === 'search' || context.config === 'all') {
			phase = 'search';
			const search = await measureSearch(extension.clipboardDialog, context);
			phases.search = search;
			checkpoints.push(checkpoint('after_search', container));

			METRICS.copyousSearchNoMatchApply.value = search.nomatch.apply_ms ?? 0;
			METRICS.copyousSearchMatchApply.value = search.match.apply_ms ?? 0;
			METRICS.copyousSearchClearApply.value = search.clear.apply_ms ?? 0;
			METRICS.copyousSearchClearFirstFrame.value = search.clear.first_frame_ms ?? 0;
			METRICS.copyousSearchClearReady.value = search.clear.ready_ms ?? 0;
		}

		phase = 'final';
		const finalCheckpoint = checkpoint('end', container, true);
		checkpoints.push(finalCheckpoint);

		const cpuStart = startup.checkpoint.cpu_ms;
		const cpuEnd = finalCheckpoint.cpu_ms;
		const cpuTotal = cpuStart != null && cpuEnd != null ? round(cpuEnd - cpuStart) : null;

		METRICS.copyousRssEnd.value = finalCheckpoint.rss_kb ?? 0;
		METRICS.copyousCpuTotal.value = cpuTotal ?? 0;
		METRICS.copyousActorsEnd.value = finalCheckpoint.actors ?? 0;
		METRICS.copyousError.value = 0;

		writeJson(sidecarPath, {
			...emptySidecar(context, null),
			started_at: GLib.DateTime.new_now_utc().format('%Y-%m-%dT%H:%M:%SZ'),
			context,
			init_to_run_ms: startup.init_to_run_ms,
			phases,
			checkpoints,
			final: {
				rss_kb: finalCheckpoint.rss_kb,
				hwm_kb: finalCheckpoint.hwm_kb,
				threads: finalCheckpoint.threads,
				cpu_ms: finalCheckpoint.cpu_ms,
				cpu_total_ms: cpuTotal,
				actors: finalCheckpoint.actors,
				counts: itemCounts(container),
				result_count: resultCount(container),
				data_count: dataCount(container),
			},
		});

		log(`BENCH done ${context.run_id} iteration=${context.iteration} config=${context.config} size=${context.size}`);
	} catch (error) {
		METRICS.copyousError.value = 1;
		log(`BENCH error during ${phase}: ${error?.stack ?? error}`);
		try {
			writeJson(sidecarPath, { ...emptySidecar(context, error), phase, context });
		} catch (writeError) {
			log(`BENCH could not write error sidecar: ${writeError}`);
		}
	}
}

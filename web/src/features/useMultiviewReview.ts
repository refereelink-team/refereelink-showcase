import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';
import type {
  ExplanationResponse,
  FoulFacts,
  MultiviewDecision,
  ReviewRecord,
  ReviewPreview,
  ReviewState,
} from '../types/multiview';
import { emptyFacts, prefillFacts } from '../components/multiview/reviewFacts';
import type { FactName } from '../components/multiview/reviewFacts';
import { ReviewRequestScope } from './reviewRequestScope';
import { normalizeLegacyDerivedFacts } from '../components/multiview/reviewGuideFlow';
import type { ReviewGuideStep } from '../components/multiview/reviewGuideFlow';

interface Draft {
  caseId: string;
  hydrated: boolean;
  facts: FoulFacts;
  decision: MultiviewDecision | null;
  review: ReviewRecord | null;
  explanation: ExplanationResponse | null;
  dirty: boolean;
  editedByReviewer: boolean;
  protectedFields: FactName[];
  step: ReviewGuideStep;
}
function blank(caseId: string): Draft {
  return {
    caseId,
    hydrated: false,
    facts: emptyFacts(),
    decision: null,
    review: null,
    explanation: null,
    dirty: false,
    editedByReviewer: false,
    protectedFields: [],
    step: 'action',
  };
}
function message(error: unknown) {
  return error instanceof Error ? error.message : '操作失败，请重试';
}

export function useMultiviewReview(caseId: string) {
  const [draft, setDraft] = useState<Draft>(() => blank(caseId));
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [explaining, setExplaining] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [newModelFields, setNewModelFields] = useState<FactName[]>([]);
  const [prefillToken, setPrefillToken] = useState<string>();
  const [states, setStates] = useState<Record<string, { state: ReviewState; revision: number }>>(
    {},
  );
  const [previewSnapshot, setPreviewSnapshot] = useState<{
    caseId: string;
    facts: FoulFacts;
    result: ReviewPreview | null;
    error: string | null;
    pending: boolean;
  } | null>(null);
  const [historySnapshot, setHistorySnapshot] = useState<{
    caseId: string;
    records: ReviewRecord[];
    pending: boolean;
    error: string | null;
  } | null>(null);
  const historySequence = useRef(0);
  const previewSequence = useRef(0);
  const [previewRetry, setPreviewRetry] = useState(0);
  const activePreview = useRef(previewSnapshot);
  activePreview.current = previewSnapshot;
  const scope = useRef(new ReviewRequestScope());
  const cache = useRef(new Map<string, Draft>());
  const analyzeLock = useRef(false);
  const saveLock = useRef(false);
  const explainSequence = useRef(0);
  const activeCase = useRef(caseId);
  activeCase.current = caseId;
  const activeDraft = useRef(draft);
  activeDraft.current = draft;
  useEffect(() => {
    if (draft.caseId && draft.hydrated && !loading) cache.current.set(draft.caseId, draft);
  }, [draft, loading]);

  const load = useCallback(async () => {
    const ticket = scope.current.ticket();
    if (!caseId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await api.review(caseId);
      if (!scope.current.current(ticket, true)) return;
      const seeded = result.review
        ? normalizeLegacyDerivedFacts(result.review.facts)
        : result.analysis
          ? prefillFacts(emptyFacts(), result.analysis, {
              protectedFields: new Set<FactName>(),
              hasSavedReview: false,
            }).facts
          : emptyFacts();
      setDraft({
        caseId,
        hydrated: true,
        facts: seeded,
        decision: result.analysis,
        review: result.review,
        explanation: null,
        dirty: result.review ? seeded !== result.review.facts : result.analysis !== null,
        editedByReviewer: false,
        protectedFields: [],
        step: result.review ? 'result' : 'action',
      });
      if (result.review)
        setStates((previous) => ({
          ...previous,
          [caseId]: { state: result.review!.review_state, revision: result.review!.revision },
        }));
    } catch (error) {
      if (scope.current.current(ticket, true)) setError(message(error));
    } finally {
      if (scope.current.current(ticket, true)) setLoading(false);
    }
  }, [caseId]);
  useEffect(() => {
    scope.current.activate(caseId);
    explainSequence.current++;
    setExplaining(false);
    setError(null);
    setNotice(null);
    setNewModelFields([]);
    setPrefillToken(undefined);
    previewSequence.current++;
    setPreviewSnapshot(null);
    const cached = cache.current.get(caseId);
    setDraft(cached ?? blank(caseId));
    if (cached) setLoading(false);
    else void load();
    return () => {
      scope.current.activate('');
    };
  }, [caseId, load]);

  useEffect(() => {
    if (!caseId || loading || !draft.hydrated || draft.caseId !== caseId) return;
    const facts = draft.facts;
    const ticket = scope.current.ticket();
    const sequence = ++previewSequence.current;
    let cancelled = false;
    setPreviewSnapshot({ caseId, facts, result: null, error: null, pending: true });
    const timer = window.setTimeout(async () => {
      try {
        const result = await api.previewReview(caseId, facts);
        if (
          cancelled ||
          sequence !== previewSequence.current ||
          !scope.current.current(ticket, true)
        )
          return;
        setPreviewSnapshot({ caseId, facts, result, error: null, pending: false });
      } catch (error) {
        if (
          cancelled ||
          sequence !== previewSequence.current ||
          !scope.current.current(ticket, true)
        )
          return;
        setPreviewSnapshot({ caseId, facts, result: null, error: message(error), pending: false });
      }
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [caseId, draft.caseId, draft.facts, draft.hydrated, loading, previewRetry]);

  function invalidatePreview() {
    previewSequence.current++;
    setPreviewSnapshot(null);
    setPreviewRetry((value) => value + 1);
  }
  function retryPreview() {
    invalidatePreview();
  }
  function setStep(step: ReviewGuideStep) {
    setDraft((current) => ({ ...current, step }));
  }

  useEffect(() => {
    if (!prefillToken) return;
    const timer = window.setTimeout(() => setNewModelFields([]), 1200);
    return () => window.clearTimeout(timer);
  }, [prefillToken]);

  function edit(facts: FoulFacts, field?: FactName) {
    if (saveLock.current) return;
    invalidatePreview();
    scope.current.edited();
    explainSequence.current++;
    setExplaining(false);
    setError(null);
    setNotice(null);
    setNewModelFields([]);
    setDraft((current) => ({
      ...current,
      facts,
      dirty: true,
      editedByReviewer: true,
      explanation: null,
      protectedFields:
        field && !current.protectedFields.includes(field)
          ? [...current.protectedFields, field]
          : current.protectedFields,
    }));
  }
  function discard() {
    if (saveLock.current || analyzeLock.current) return;
    invalidatePreview();
    scope.current.edited();
    explainSequence.current++;
    setExplaining(false);
    setDraft((current) => ({
      ...current,
      facts:
        (current.review ? normalizeLegacyDerivedFacts(current.review.facts) : null) ??
        (current.decision
          ? prefillFacts(emptyFacts(), current.decision, {
              protectedFields: new Set<FactName>(),
              hasSavedReview: false,
            }).facts
          : emptyFacts()),
      dirty: Boolean(
        current.review &&
        normalizeLegacyDerivedFacts(current.review.facts) !== current.review.facts,
      ),
      editedByReviewer: false,
      protectedFields: [],
      explanation: null,
    }));
    setNewModelFields([]);
    setNotice('已还原到已保存的事实；未保存的案例恢复模型建议');
  }
  async function analyze() {
    if (!caseId || analyzeLock.current || saveLock.current || loading) return;
    analyzeLock.current = true;
    setAnalyzing(true);
    setError(null);
    setNotice(null);
    const ticket = scope.current.ticket();
    try {
      const result = await api.analyze(caseId);
      if (!scope.current.current(ticket)) return;
      if (result.status !== 'ok' || !result.decision)
        throw new Error(result.message || '分析未产生结果');
      const current = activeDraft.current;
      const seeded = prefillFacts(current.facts, result.decision, {
        protectedFields: new Set(current.protectedFields),
        hasSavedReview: Boolean(current.review),
      });
      invalidatePreview();
      scope.current.edited();
      explainSequence.current++;
      setExplaining(false);
      setDraft({
        ...current,
        decision: result.decision,
        facts: seeded.facts,
        dirty: true,
        explanation: null,
      });
      setNewModelFields(seeded.changedFields);
      setPrefillToken(result.decision.analysis_id);
      return result.decision;
    } catch (error) {
      if (scope.current.current(ticket)) setError(message(error));
    } finally {
      analyzeLock.current = false;
      setAnalyzing(false);
    }
  }
  async function explainRecord(review: ReviewRecord) {
    const ticket = scope.current.ticket();
    const sequence = ++explainSequence.current;
    setExplaining(true);
    try {
      const result = await api.explain(review.case_id, review.revision);
      if (
        scope.current.current(ticket, true) &&
        sequence === explainSequence.current &&
        result.case_id === review.case_id &&
        result.revision === review.revision
      )
        setDraft((current) =>
          current.review?.revision === review.revision && !current.dirty
            ? { ...current, explanation: result }
            : current,
        );
    } catch (error) {
      if (scope.current.current(ticket, true) && sequence === explainSequence.current)
        setNotice(`事实已保存，规则模板可用；解释请求未完成：${message(error)}`);
    } finally {
      if (scope.current.current(ticket) && sequence === explainSequence.current)
        setExplaining(false);
    }
  }
  async function save(state: ReviewState) {
    if (!caseId || saveLock.current || analyzing || loading) return;
    const current = activeDraft.current;
    const snapshot = activePreview.current;
    if (
      state === 'reviewed' &&
      (!snapshot ||
        snapshot.caseId !== caseId ||
        snapshot.facts !== current.facts ||
        snapshot.pending ||
        !snapshot.result?.can_finalize)
    ) {
      setError('当前建议尚未确定，请先补充事实或保存为待确认。');
      return;
    }
    saveLock.current = true;
    setSaving(true);
    setError(null);
    setNotice(null);
    const ticket = scope.current.ticket();
    try {
      const result = await api.saveReview(caseId, {
        expected_revision: current.review?.revision ?? 0,
        analysis_id: current.decision?.analysis_id ?? null,
        facts: current.facts,
        review_state: state,
        preserve_unknowns: true,
      });
      setStates((previous) => ({
        ...previous,
        [caseId]: { state: result.review.review_state, revision: result.review.revision },
      }));
      if (activeCase.current === caseId) historySequence.current++;
      setHistorySnapshot((previous) =>
        previous?.caseId === caseId
          ? {
              ...previous,
              pending: false,
              error: null,
              records: [
                result.review,
                ...previous.records.filter((record) => record.revision !== result.review.revision),
              ],
            }
          : previous,
      );
      const canonical: Draft = {
        ...current,
        review: result.review,
        facts: result.review.facts,
        dirty: false,
        editedByReviewer: false,
        explanation: null,
        protectedFields: [],
        hydrated: true,
      };
      // A save may finish after switching cases or editing a draft. Preserve newer input.
      const visible = activeDraft.current;
      const latest =
        activeCase.current === caseId && visible.caseId === caseId
          ? visible
          : cache.current.get(caseId);
      const updated =
        latest && latest.facts !== current.facts
          ? { ...latest, review: result.review, dirty: true, explanation: null }
          : { ...canonical, step: latest?.step ?? current.step };
      cache.current.set(caseId, updated);
      if (
        scope.current.current(ticket, true) ||
        (activeCase.current === caseId && visible.caseId === caseId)
      ) {
        invalidatePreview();
        scope.current.edited();
        setDraft(updated);
        setNewModelFields([]);
        setNotice(
          updated.dirty
            ? '复核已保存，后续修改仍保留在草稿中'
            : result.review.review_state === 'uncertain'
              ? '已保存为待确认'
              : '复核已保存',
        );
        if (
          !updated.dirty &&
          result.review.assessment.status === 'complete' &&
          state === 'reviewed'
        )
          void explainRecord(result.review);
      }
    } catch (error) {
      if (scope.current.current(ticket)) setError(message(error));
    } finally {
      saveLock.current = false;
      setSaving(false);
    }
  }
  async function explain() {
    const current = activeDraft.current;
    if (
      current.review &&
      current.review.assessment.status === 'complete' &&
      !current.dirty &&
      !saving
    )
      await explainRecord(current.review);
  }
  async function loadHistory() {
    const ticket = scope.current.ticket();
    const sequence = ++historySequence.current;
    setHistorySnapshot((previous) => ({
      caseId,
      records: previous?.caseId === caseId ? previous.records : [],
      pending: true,
      error: null,
    }));
    try {
      const result = await api.reviewHistory(caseId);
      if (scope.current.current(ticket) && sequence === historySequence.current)
        setHistorySnapshot({ caseId, records: result.history, pending: false, error: null });
    } catch (error) {
      if (scope.current.current(ticket) && sequence === historySequence.current)
        setHistorySnapshot({ caseId, records: [], pending: false, error: message(error) });
    }
  }
  function showError(error: unknown) {
    setError(message(error));
  }
  // Retry after a failed load is safe; a conflict refresh requires explicitly restoring the draft first.
  async function refresh() {
    if (analyzeLock.current || saveLock.current) {
      setNotice('正在处理当前操作，完成后可重新读取复核。');
      return;
    }
    if (activeDraft.current.editedByReviewer) {
      setError('草稿已保留。先还原草稿，再重试读取服务器最新复核。');
      return;
    }
    cache.current.delete(caseId);
    scope.current.edited();
    await load();
  }
  const visible = draft.caseId === caseId ? draft : blank(caseId);
  const currentPreview =
    previewSnapshot?.caseId === caseId && previewSnapshot.facts === visible.facts
      ? previewSnapshot
      : null;
  return {
    ...visible,
    loading: loading || draft.caseId !== caseId,
    analyzing,
    saving,
    explaining,
    error,
    notice,
    newModelFields,
    prefillToken,
    preview: currentPreview?.result ?? null,
    previewError: currentPreview?.error ?? null,
    previewLoading: !loading && visible.hydrated && (!currentPreview || currentPreview.pending),
    setStep,
    retryPreview,
    history: historySnapshot?.caseId === caseId ? historySnapshot.records : [],
    historyLoading: historySnapshot?.caseId === caseId && historySnapshot.pending,
    historyError: historySnapshot?.caseId === caseId ? historySnapshot.error : null,
    loadHistory,
    states,
    edit,
    discard,
    analyze,
    save,
    explain,
    refresh,
    showError,
  };
}

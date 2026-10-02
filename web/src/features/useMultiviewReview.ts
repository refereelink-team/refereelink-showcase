import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';
import type {
  ExplanationResponse,
  FoulFacts,
  MultiviewDecision,
  ReviewRecord,
  ReviewState,
} from '../types/multiview';
import { emptyFacts, prefillFacts } from '../components/multiview/reviewFacts';
import type { FactName } from '../components/multiview/reviewFacts';
import { ReviewRequestScope } from './reviewRequestScope';

interface Draft {
  caseId: string;
  hydrated: boolean;
  facts: FoulFacts;
  decision: MultiviewDecision | null;
  review: ReviewRecord | null;
  explanation: ExplanationResponse | null;
  dirty: boolean;
  protectedFields: FactName[];
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
    protectedFields: [],
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
        ? result.review.facts
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
        dirty: !result.review && result.analysis !== null,
        protectedFields: [],
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
    const cached = cache.current.get(caseId);
    setDraft(cached ?? blank(caseId));
    if (cached) setLoading(false);
    else void load();
    return () => {
      scope.current.activate('');
    };
  }, [caseId, load]);

  useEffect(() => {
    if (!prefillToken) return;
    const timer = window.setTimeout(() => setNewModelFields([]), 1200);
    return () => window.clearTimeout(timer);
  }, [prefillToken]);

  function edit(facts: FoulFacts, field?: FactName) {
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
      explanation: null,
      protectedFields:
        field && !current.protectedFields.includes(field)
          ? [...current.protectedFields, field]
          : current.protectedFields,
    }));
  }
  function discard() {
    scope.current.edited();
    explainSequence.current++;
    setExplaining(false);
    setDraft((current) => ({
      ...current,
      facts:
        current.review?.facts ??
        (current.decision
          ? prefillFacts(emptyFacts(), current.decision, {
              protectedFields: new Set<FactName>(),
              hasSavedReview: false,
            }).facts
          : emptyFacts()),
      dirty: false,
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
      setNotice(
        seeded.changedFields.length
          ? '模型分析完成，已预填建议；请逐项确认后保存'
          : '模型分析完成，现有人工事实已保留；保存后更新规则评估',
      );
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
    saveLock.current = true;
    setSaving(true);
    setError(null);
    setNotice(null);
    const ticket = scope.current.ticket();
    const current = activeDraft.current;
    try {
      const result = await api.saveReview(caseId, {
        expected_revision: current.review?.revision ?? 0,
        analysis_id: current.decision?.analysis_id ?? null,
        facts: current.facts,
        review_state: state,
      });
      setStates((previous) => ({
        ...previous,
        [caseId]: { state: result.review.review_state, revision: result.review.revision },
      }));
      if (!scope.current.current(ticket)) {
        const visible = activeDraft.current;
        const canonical = {
          ...current,
          review: result.review,
          facts: result.review.facts,
          dirty: false,
          explanation: null,
          protectedFields: [],
          hydrated: true,
        };
        cache.current.set(caseId, canonical);
        if (activeCase.current === caseId && visible.facts === current.facts) {
          setDraft(canonical);
          scope.current.edited();
          setNotice(`复核已保存 · 修订 ${result.review.revision}`);
          void explainRecord(result.review);
        }
        return;
      }
      scope.current.edited();
      setDraft({
        ...current,
        review: result.review,
        facts: result.review.facts,
        dirty: false,
        explanation: null,
        protectedFields: [],
      });
      setNewModelFields([]);
      setNotice(
        `复核已保存 · 修订 ${result.review.revision}${result.review.assessment.status !== 'complete' ? ' · 规则事实仍需补充，可在规则依据中查看' : ''}`,
      );
      void explainRecord(result.review);
    } catch (error) {
      if (scope.current.current(ticket)) setError(message(error));
    } finally {
      saveLock.current = false;
      setSaving(false);
    }
  }
  async function explain() {
    const current = activeDraft.current;
    if (current.review && !current.dirty && !saving) await explainRecord(current.review);
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
    if (activeDraft.current.dirty) {
      setError('草稿已保留。先还原草稿，再重试读取服务器最新复核。');
      return;
    }
    cache.current.delete(caseId);
    scope.current.edited();
    await load();
  }
  const visible = draft.caseId === caseId ? draft : blank(caseId);
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

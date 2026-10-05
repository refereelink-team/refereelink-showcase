import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import type { FoulFacts, ReviewPreview } from '../types/multiview';
import { prefillFacts } from '../components/multiview/reviewFacts';
import type { FactName } from '../components/multiview/reviewFacts';
import type { ReviewGuideStep } from '../components/multiview/reviewGuideFlow';
import { ReviewRequestScope } from './reviewRequestScope';
import type { ReviewRequestTicket } from './reviewRequestScope';
import { createDemoReviewDraft } from './demoReviewDraft';

function message(error: unknown) {
  return error instanceof Error ? error.message : '操作失败，请重试';
}

// The workbench is keyed by case ID. No saved review or earlier analysis is restored.
export function useMultiviewReview(caseId: string) {
  const [draft, setDraft] = useState(createDemoReviewDraft);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newModelFields, setNewModelFields] = useState<FactName[]>([]);
  const [prefillToken, setPrefillToken] = useState<string>();
  const [previewSnapshot, setPreviewSnapshot] = useState<{
    facts: FoulFacts;
    result: ReviewPreview | null;
    error: string | null;
    pending: boolean;
  } | null>(null);
  const previewSequence = useRef(0);
  const [previewRetry, setPreviewRetry] = useState(0);
  const scope = useRef(new ReviewRequestScope());
  const analyzeRequest = useRef<ReviewRequestTicket | null>(null);
  const activeDraft = useRef(draft);
  activeDraft.current = draft;

  useEffect(() => {
    scope.current.activate(caseId);
    return () => scope.current.activate('');
  }, [caseId]);

  useEffect(() => {
    if (!caseId) return;
    const facts = draft.facts;
    const ticket = scope.current.ticket();
    const sequence = ++previewSequence.current;
    let cancelled = false;
    setPreviewSnapshot({ facts, result: null, error: null, pending: true });
    const timer = window.setTimeout(async () => {
      try {
        const result = await api.previewReview(caseId, facts);
        if (
          cancelled ||
          sequence !== previewSequence.current ||
          !scope.current.current(ticket, true)
        )
          return;
        setPreviewSnapshot({ facts, result, error: null, pending: false });
      } catch (error) {
        if (
          cancelled ||
          sequence !== previewSequence.current ||
          !scope.current.current(ticket, true)
        )
          return;
        setPreviewSnapshot({ facts, result: null, error: message(error), pending: false });
      }
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [caseId, draft.facts, previewRetry]);

  useEffect(() => {
    if (!prefillToken) return;
    const timer = window.setTimeout(() => setNewModelFields([]), 1200);
    return () => window.clearTimeout(timer);
  }, [prefillToken]);

  function retryPreview() {
    previewSequence.current++;
    setPreviewSnapshot(null);
    setPreviewRetry((value) => value + 1);
  }
  function setStep(step: ReviewGuideStep) {
    setDraft((current) => ({ ...current, step }));
  }
  function edit(facts: FoulFacts, field?: FactName) {
    retryPreview();
    scope.current.edited();
    setError(null);
    setNewModelFields([]);
    setDraft((current) => ({
      ...current,
      facts,
      protectedFields:
        field && !current.protectedFields.includes(field)
          ? [...current.protectedFields, field]
          : current.protectedFields,
    }));
  }
  function reset() {
    scope.current.activate(caseId);
    analyzeRequest.current = null;
    setAnalyzing(false);
    setError(null);
    setNewModelFields([]);
    setPrefillToken(undefined);
    retryPreview();
    setDraft(createDemoReviewDraft());
  }
  async function analyze() {
    if (!caseId || analyzeRequest.current) return;
    const ticket = scope.current.ticket();
    analyzeRequest.current = ticket;
    setAnalyzing(true);
    setError(null);
    try {
      const result = await api.analyze(caseId);
      if (!scope.current.current(ticket)) return;
      if (result.status !== 'ok' || !result.decision)
        throw new Error(result.message || '分析未产生结果');
      const current = activeDraft.current;
      const seeded = prefillFacts(current.facts, result.decision, {
        protectedFields: new Set(current.protectedFields),
        hasSavedReview: false,
      });
      retryPreview();
      scope.current.edited();
      setDraft({ ...current, decision: result.decision, facts: seeded.facts });
      setNewModelFields(seeded.changedFields);
      setPrefillToken(result.decision.analysis_id);
      return result.decision;
    } catch (error) {
      if (scope.current.current(ticket)) setError(message(error));
    } finally {
      if (analyzeRequest.current === ticket) {
        analyzeRequest.current = null;
        if (scope.current.current(ticket)) setAnalyzing(false);
      }
    }
  }
  const currentPreview = previewSnapshot?.facts === draft.facts ? previewSnapshot : null;
  return {
    ...draft,
    analyzing,
    error,
    newModelFields,
    prefillToken,
    preview: currentPreview?.result ?? null,
    previewError: currentPreview?.error ?? null,
    previewLoading: Boolean(caseId) && (!currentPreview || currentPreview.pending),
    setStep,
    retryPreview,
    edit,
    reset,
    analyze,
    showError: (error: unknown) => setError(message(error)),
  };
}

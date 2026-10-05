import type { FoulFacts, MultiviewDecision } from '../types/multiview';
import { emptyFacts } from '../components/multiview/reviewFacts.ts';
import type { FactName } from '../components/multiview/reviewFacts';
import type { ReviewGuideStep } from '../components/multiview/reviewGuideFlow';

export interface DemoReviewDraft {
  facts: FoulFacts;
  decision: MultiviewDecision | null;
  protectedFields: FactName[];
  step: ReviewGuideStep;
}

// Each case visit starts a new demonstration, including when returning to the same case.
export function createDemoReviewDraft(): DemoReviewDraft {
  return {
    facts: emptyFacts(),
    decision: null,
    protectedFields: [],
    step: 'action',
  };
}

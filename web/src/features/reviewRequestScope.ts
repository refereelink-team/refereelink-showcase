// A generation, rather than a case ID alone, also rejects stale A -> B -> A responses.
export interface ReviewRequestTicket {
  caseId: string;
  generation: number;
  draft: number;
}
export class ReviewRequestScope {
  private caseId = '';
  private generation = 0;
  private draft = 0;
  activate(caseId: string) {
    this.caseId = caseId;
    this.generation++;
    this.draft = 0;
  }
  edited() {
    this.draft++;
  }
  ticket(): ReviewRequestTicket {
    return { caseId: this.caseId, generation: this.generation, draft: this.draft };
  }
  current(ticket: ReviewRequestTicket, includeDraft = false) {
    return (
      ticket.caseId === this.caseId &&
      ticket.generation === this.generation &&
      (!includeDraft || ticket.draft === this.draft)
    );
  }
}

// Reject late preparation/poll responses, including A -> B -> A visits.
export interface ExperimentTicket {
  scope: string;
  generation: number;
}
export class ExperimentRequestScope {
  private scope = '';
  private generation = 0;
  activate(scope: string) {
    if (this.scope === scope) return;
    this.scope = scope;
    this.generation++;
  }
  ticket(): ExperimentTicket {
    return { scope: this.scope, generation: this.generation };
  }
  current(ticket: ExperimentTicket) {
    return ticket.scope === this.scope && ticket.generation === this.generation;
  }
}

// Reject late history/poll responses after submission, including A -> B -> A visits.
export interface ExperimentTicket {
  scope: string;
  generation: number;
  revision: number;
}
export class ExperimentRequestScope {
  private scope = '';
  private generation = 0;
  private revision = 0;
  activate(scope: string) {
    if (this.scope === scope) return;
    this.scope = scope;
    this.generation++;
    this.revision = 0;
  }
  submitted() {
    this.revision++;
    return this.ticket();
  }
  ticket(): ExperimentTicket {
    return { scope: this.scope, generation: this.generation, revision: this.revision };
  }
  current(ticket: ExperimentTicket) {
    return (
      ticket.scope === this.scope &&
      ticket.generation === this.generation &&
      ticket.revision === this.revision
    );
  }
}

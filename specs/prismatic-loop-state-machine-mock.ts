/**
 * Prismatic Engine — 7-Step Iterative Loop State Machine Prototype
 * 
 * This file illustrates how the orchestrator manages transitions between
 * steps in the 7-step iterative loop, honoring the current Orchestration Mode.
 * 
 * Connections:
 * - Decompose: SwarmPlanner
 * - Dispatch & Integrate: SwarmOrchestrator, ContractManager
 * - Execute: SwarmLockManager, HandoffProtocol
 * - Review & Refine: JulesExecutor, HandoffProtocol
 */

export type PrismaticState =
    | 'IDLE'
    | 'DECOMPOSING'
    | 'PENDING_PLAN_APPROVAL'
    | 'DISPATCHING'
    | 'EXECUTING'
    | 'REVIEWING'
    | 'FEEDBACK'
    | 'REFINING'
    | 'PENDING_INTEGRATION'
    | 'INTEGRATING';

export type PrismaticEvent =
    | 'MEGAPROMPT_RECEIVED'
    | 'PLAN_GENERATED'
    | 'PLAN_APPROVED'
    | 'PLAN_REJECTED'
    | 'AGENTS_PROVISIONED'
    | 'BRANCH_PUSHED'
    | 'REVIEW_FAILED'
    | 'REVIEW_PASSED'
    | 'FEEDBACK_DELIVERED'
    | 'BRANCH_REVISED'
    | 'INTEGRATION_APPROVED'
    | 'INTEGRATION_REJECTED'
    | 'MERGE_SUCCESS';

export type OrchestrationMode = 'Interactive' | 'Collaborative' | 'Autonomous';

export interface AgentContract {
    threadId: string;
    role: string;
    taskDescription: string;
    allowedDirectories: string[];
    readOnlyDirectories: string[];
    targetHead: string;
}

export interface ReviewReport {
    threadId: string;
    reviewerId: string;
    status: 'approved' | 'rejected';
    summary: string;
    errors?: string[];
}

export class PrismaticLoopStateMachine {
    private _currentState: PrismaticState = 'IDLE';
    private _mode: OrchestrationMode;
    private _activeContracts: AgentContract[] = [];
    private _iterationCounts: Map<string, number> = new Map();
    private readonly MAX_ITERATIONS = 3;

    constructor(mode: OrchestrationMode = 'Collaborative') {
        this._mode = mode;
        console.log(`[Prismatic FSM] Initialized in ${this._mode} mode.`);
    }

    public get currentState(): PrismaticState {
        return this._currentState;
    }

    public setMode(mode: OrchestrationMode): void {
        this._mode = mode;
        console.log(`[Prismatic FSM] Switched mode to: ${this._mode}`);
    }

    /**
     * Transition the state machine based on incoming events and evaluate HITL gates.
     */
    public async transition(event: PrismaticEvent, payload?: any): Promise<void> {
        const previousState = this._currentState;
        
        switch (this._currentState) {
            case 'IDLE':
                if (event === 'MEGAPROMPT_RECEIVED') {
                    this._currentState = 'DECOMPOSING';
                    await this.handleDecompose(payload as string);
                }
                break;

            case 'DECOMPOSING':
                if (event === 'PLAN_GENERATED') {
                    this._activeContracts = payload as AgentContract[];
                    if (this._mode === 'Interactive') {
                        // Human-in-the-loop gate 1
                        this._currentState = 'PENDING_PLAN_APPROVAL';
                        this.notifyHumanOperator('Plan requires approval.', this._activeContracts);
                    } else {
                        // Collaborative / Autonomous bypasses approval
                        this._currentState = 'DISPATCHING';
                        await this.handleDispatch();
                    }
                }
                break;

            case 'PENDING_PLAN_APPROVAL':
                if (event === 'PLAN_APPROVED') {
                    this._currentState = 'DISPATCHING';
                    await this.handleDispatch();
                } else if (event === 'PLAN_REJECTED') {
                    this._currentState = 'IDLE';
                    this.clearActiveTasks();
                }
                break;

            case 'DISPATCHING':
                if (event === 'AGENTS_PROVISIONED') {
                    this._currentState = 'EXECUTING';
                    this.notifyAgentsToStart();
                }
                break;

            case 'EXECUTING':
                if (event === 'BRANCH_PUSHED') {
                    this._currentState = 'REVIEWING';
                    await this.handleReview(payload as string); // branch name
                }
                break;

            case 'REVIEWING':
                if (event === 'REVIEW_FAILED') {
                    this._currentState = 'FEEDBACK';
                    await this.handleFeedback(payload as ReviewReport);
                } else if (event === 'REVIEW_PASSED') {
                    if (this._mode === 'Interactive') {
                        // Human-in-the-loop gate 2
                        this._currentState = 'PENDING_INTEGRATION';
                        this.notifyHumanOperator('Review passed. Confirm integration.', payload);
                    } else {
                        // Collaborative / Autonomous merges automatically on successful review
                        this._currentState = 'INTEGRATING';
                        await this.handleIntegration();
                    }
                }
                break;

            case 'FEEDBACK':
                if (event === 'FEEDBACK_DELIVERED') {
                    this._currentState = 'REFINING';
                    this.triggerWorkerRefinement(payload as string); // threadId
                }
                break;

            case 'REFINING':
                if (event === 'BRANCH_REVISED') {
                    this._currentState = 'REVIEWING';
                    await this.handleReview(payload as string); // branch name
                }
                break;

            case 'PENDING_INTEGRATION':
                if (event === 'INTEGRATION_APPROVED') {
                    this._currentState = 'INTEGRATING';
                    await this.handleIntegration();
                } else if (event === 'INTEGRATION_REJECTED') {
                    this._currentState = 'FEEDBACK';
                    await this.handleFeedback({
                        threadId: this._activeContracts[0]?.threadId || '',
                        reviewerId: 'HumanOperator',
                        status: 'rejected',
                        summary: 'Human operator rejected integration: ' + (payload || 'No reason provided')
                    });
                }
                break;

            case 'INTEGRATING':
                if (event === 'MERGE_SUCCESS') {
                    this._currentState = 'IDLE';
                    this.finalizeCleanup();
                }
                break;
        }

        if (previousState !== this._currentState) {
            console.log(`[Prismatic FSM Transition] ${previousState} ──(${event})──> ${this._currentState}`);
        }
    }

    /* --- Step Implementations --- */

    private async handleDecompose(megaprompt: string): Promise<void> {
        console.log(`[Step 1: Decompose] Analyzing megaprompt using SwarmPlanner...`);
        // Simulating SwarmPlanner.ts decomposition
        setTimeout(async () => {
            const mockContracts: AgentContract[] = [
                {
                    threadId: `thread-${Date.now()}`,
                    role: 'Content Specialist',
                    taskDescription: 'Write mock documentation for the 7-step loop.',
                    allowedDirectories: ['content/specs/'],
                    readOnlyDirectories: ['src/'],
                    targetHead: 'Headless API'
                }
            ];
            await this.transition('PLAN_GENERATED', mockContracts);
        }, 500);
    }

    private async handleDispatch(): Promise<void> {
        console.log(`[Step 2: Dispatch] Writing contract files and provisioning executors via SwarmOrchestrator...`);
        // Simulating SwarmOrchestrator.ts & ContractManager.ts
        for (const contract of this._activeContracts) {
            console.log(`  - Registered contract: .antigravity/contracts/${contract.threadId}.json`);
            this._iterationCounts.set(contract.threadId, 1);
        }
        setTimeout(async () => {
            await this.transition('AGENTS_PROVISIONED');
        }, 300);
    }

    private notifyAgentsToStart(): void {
        console.log(`[Step 3: Execute] Waking up Worker agents. Agents checkout files using swarm.js and begin edit cycle...`);
    }

    private async handleReview(branchName: string): Promise<void> {
        console.log(`[Step 4: Review] Reviewing branch '${branchName}' using JulesExecutor and test suites...`);
        // Simulating review logic
        setTimeout(async () => {
            const isCompilationSuccess = Math.random() > 0.3; // 70% chance to pass for demonstration
            if (isCompilationSuccess) {
                console.log(`  - Verification passed for branch ${branchName}.`);
                await this.transition('REVIEW_PASSED', branchName);
            } else {
                console.warn(`  - Verification failed for branch ${branchName}.`);
                const mockReport: ReviewReport = {
                    threadId: this._activeContracts[0]?.threadId || 'unknown',
                    reviewerId: 'jules',
                    status: 'rejected',
                    summary: 'Compilation failed on line 22 of mock_file.ts: type mismatch.'
                };
                await this.transition('REVIEW_FAILED', mockReport);
            }
        }, 500);
    }

    private async handleFeedback(report: ReviewReport): Promise<void> {
        console.log(`[Step 5: Feedback] Formatting feedback payload. Source: ${report.reviewerId}`);
        const currentIteration = this._iterationCounts.get(report.threadId) || 1;
        
        if (currentIteration >= this.MAX_ITERATIONS) {
            console.warn(`[Safeguard Escalation] Thread ${report.threadId} exceeded maximum refinement iterations (${this.MAX_ITERATIONS}). Escalating to Human Operator.`);
            this.setMode('Interactive');
            this.notifyHumanOperator(`Max iterations reached for ${report.threadId}. Manual intervention required.`, report);
            return;
        }

        this._iterationCounts.set(report.threadId, currentIteration + 1);
        console.log(`  - Feedback log created: .antigravity/feedback_${report.threadId}_iter_${currentIteration}.json`);
        
        setTimeout(async () => {
            await this.transition('FEEDBACK_DELIVERED', report.threadId);
        }, 200);
    }

    private triggerWorkerRefinement(threadId: string): void {
        console.log(`[Step 6: Refine] Agent notified of iteration ${this._iterationCounts.get(threadId)} starting based on feedback file.`);
    }

    private async handleIntegration(): Promise<void> {
        console.log(`[Step 7: Integrate] Merging staging branch using Governor agent (Fred)...`);
        // Simulating HandoffProtocol.ts and git merge
        setTimeout(async () => {
            console.log(`  - Git merge completed to deploy-fresh staging branch.`);
            await this.transition('MERGE_SUCCESS');
        }, 400);
    }

    private finalizeCleanup(): void {
        console.log(`[Step 7: Cleanup] Deleting contract files and releasing all swarm locks.`);
        for (const contract of this._activeContracts) {
            console.log(`  - Deleted contract: .antigravity/contracts/${contract.threadId}.json`);
        }
        this._activeContracts = [];
        console.log(`[Prismatic FSM] Back to IDLE state. Ready for next Megaprompt.`);
    }

    /* --- Helpers --- */

    private notifyHumanOperator(reason: string, context: any): void {
        console.log(`\n================ HUMAN INTERVENTION GATED ================`);
        console.log(`Reason: ${reason}`);
        console.log(`Context:`, JSON.stringify(context, null, 2));
        console.log(`==========================================================\n`);
    }

    private clearActiveTasks(): void {
        this._activeContracts = [];
        this._iterationCounts.clear();
        console.log(`[Prismatic FSM] Swarm plan cleared.`);
    }
}

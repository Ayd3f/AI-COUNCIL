export type AgentName =
  | 'OPENAI'
  | 'CLAUDE'
  | 'GEMINI'
  | 'GROK'
  | 'DEEPSEEK'
  | 'GROQCLOUD'
  | 'CEREBRAS'
  | 'MISTRAL'
  | 'LOCAL'

export type AgentStatus =
  | 'IDLE'
  | 'THINKING'
  | 'OK'
  | 'ERROR'
  | 'INVALID_OUTPUT'
  | 'DISABLED'

export type DebateStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED'

export interface ArgumentRef {
  agent: string
  argument: string
  reason: string
}

export interface InitialAnswer {
  answer: string
  key_points: string[]
  confidence: number
  assumptions: string[]
}

export interface PersuasionAttempt {
  agent: string
  their_objection: string
  my_counter: string
  concession: string
}

export interface DebateResponse {
  position: string
  accepted_arguments: ArgumentRef[]
  rejected_arguments: ArgumentRef[]
  uncertain_arguments: ArgumentRef[]
  /** Targeted attempts to move a specific other agent. */
  persuasion: PersuasionAttempt[]
  /** Agents whose argument actually moved this one. */
  persuaded_by: string[]
  what_would_change_my_mind: string
  proposed_common_answer: string
  changed_my_position: boolean
  why_changed: string
  confidence: number
}

export type AgentPayload = Partial<InitialAnswer & DebateResponse>

export interface TokenUsage {
  input_tokens: number
  output_tokens: number
}

export interface Diagnosis {
  code: string
  title: string
  action: string
  url: string
  permanent: boolean
}

export interface AgentOutcome {
  agent: AgentName
  status: AgentStatus
  model: string
  payload: AgentPayload | null
  error: string | null
  error_kind: string | null
  attempts: number
  latency_ms: number
  usage: TokenUsage
  /** Derived server-side: what the provider error means, in plain language. */
  diagnosis: Diagnosis | null
}

export interface ConsensusReport {
  round_index: number
  reached: boolean
  score: number
  threshold: number
  agree_count: number
  participant_count: number
  rule: string
  reason: string
  agreement_by_agent: Record<string, number>
  agreeing_agents: string[]
  dissenting_agents: string[]
  unsubstantiated_agents: string[]
  /** Switched position without naming who persuaded them — not counted. */
  capitulated_agents: string[]
  persuasion_edges: { agent: string; persuaded_by: string }[]
  material_objections: { agent: string; objection: string; evidence: string }[]
  main_conclusion: string
}

export interface RoundResult {
  index: number
  kind: 'INITIAL' | 'DEBATE'
  outcomes: Record<string, AgentOutcome>
  consensus: ConsensusReport | null
  started_at: string
  finished_at: string
}

export interface MinorityPosition {
  agent: string
  position: string
  evidence: string
  assessment: string
}

export interface RejectedArgumentSummary {
  argument: string
  rejected_by: string[]
  reason: string
}

export interface SynthesisResult {
  consensus: string
  main_reasoning: string
  key_agreements: string[]
  disagreements: string[]
  strongest_arguments: string[]
  rejected_arguments: RejectedArgumentSummary[]
  minority_positions: MinorityPosition[]
  individual_positions: { agent: string; position: string }[]
  confidence: number
}

export interface CostLine {
  agent: string
  model: string
  input_tokens: number
  output_tokens: number
  total_tokens: number
  estimated_cost_usd: number | null
  pricing_known: boolean
}

export interface CostReport {
  lines: CostLine[]
  total_tokens: number
  estimated_cost_usd: number
  complete: boolean
  models_without_pricing: string[]
  currency: string
}

export interface DebateConfig {
  agents: AgentName[]
  min_rounds: number
  max_rounds: number
  consensus_threshold: number
  timeout: number
  temperature: number
  max_retries: number
  models: Record<string, string>
  /** Agent -> round-table role (ADVOCATE, SKEPTIC, ...). */
  roles: Record<string, string>
}

export interface DebateDetail {
  id: string
  question: string
  status: DebateStatus
  created_at: string
  finished_at: string | null
  rounds_used: number
  max_rounds: number
  consensus_reached: boolean
  consensus_score: number
  config: DebateConfig | null
  rounds: RoundResult[]
  synthesis: SynthesisResult | null
  synthesis_agent: string | null
  cost: CostReport | null
  error: string | null
  running?: boolean
}

export interface DebateSummary {
  id: string
  question: string
  status: DebateStatus
  created_at: string
  finished_at: string | null
  rounds_used: number
  max_rounds: number
  consensus_reached: boolean
  consensus_score: number
}

export interface AgentRosterEntry {
  agent: AgentName
  provider: string
  model: string
  configured: boolean
  key_env: string
  model_env: string
  free_tier: boolean
  signup_url: string
}

export interface AppSettings {
  agents: AgentRosterEntry[]
  defaults: {
    min_rounds: number
    max_rounds: number
    consensus_threshold: number
    timeout: number
    temperature: number
    max_retries: number
    max_tokens: number
  }
  limits: {
    max_question_length: number
    max_rounds_allowed: number
    timeout_range: [number, number]
    rate_limit: { requests: number; window_seconds: number }
  }
  synthesis_agent: string
  pricing_configured: boolean
  any_provider_configured: boolean
}

export interface DebateEvent {
  seq: number
  type: string
  debate_id: string
  data: Record<string, unknown>
  created_at: string
}

export interface StartDebatePayload {
  question: string
  agents?: AgentName[]
  min_rounds?: number
  max_rounds?: number
  consensus_threshold?: number
  timeout?: number
  temperature?: number
  max_retries?: number
  models?: Record<string, string>
}

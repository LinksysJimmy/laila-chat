export interface AgentCoreInvokeRequest {
  message: string;
  session_id?: string;
}

export interface AgentCoreInvokeResponse {
  response: string;
  session_id: string;
}

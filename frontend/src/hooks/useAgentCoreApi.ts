import useHttp from './useHttp';
import type {
  AgentCoreInvokeRequest,
  AgentCoreInvokeResponse,
} from '../@types/agentcore';

const useAgentCoreApi = () => {
  const http = useHttp();

  return {
    invoke: (
      params: AgentCoreInvokeRequest
    ): Promise<AgentCoreInvokeResponse> =>
      http
        .post<AgentCoreInvokeResponse>('agentcore/invoke', params)
        .then((res) => res.data),
  };
};

export default useAgentCoreApi;

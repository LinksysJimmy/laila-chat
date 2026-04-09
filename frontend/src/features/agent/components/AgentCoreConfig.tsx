import { useTranslation } from 'react-i18next';
import InputText from '../../../components/InputText';
import { AgentCoreConfig as AgentCoreConfigType } from '../types';

type Props = {
  config: AgentCoreConfigType;
  onChange: (config: AgentCoreConfigType) => void;
};

export const AgentCoreConfig = ({ config, onChange }: Props) => {
  const { t } = useTranslation();

  return (
    <div className="space-y-4">
      <InputText
        label={t('agent.tools.agentCore.agentRuntimeId.label')}
        placeholder={t('agent.tools.agentCore.agentRuntimeId.placeholder')}
        value={config.agentRuntimeId}
        onChange={(value) => onChange({ ...config, agentRuntimeId: value })}
      />
    </div>
  );
};

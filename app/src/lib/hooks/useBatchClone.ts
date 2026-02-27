import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/lib/api/client';

const POLL_INTERVAL_MS = 1500;

export function useBatchClone() {
  const queryClient = useQueryClient();

  const startMutation = useMutation({
    mutationFn: (formData: FormData) => apiClient.startBatchClone(formData),
  });

  const batchId = startMutation.data?.batch_id;

  const statusQuery = useQuery({
    queryKey: ['batch-clone-status', batchId],
    queryFn: () => apiClient.getBatchCloneStatus(batchId!),
    enabled: !!batchId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === 'complete' || status === 'error') {
        return false;
      }
      return POLL_INTERVAL_MS;
    },
  });

  const status = statusQuery.data?.status;
  const progress = statusQuery.data?.progress;
  const error = statusQuery.data?.error;

  const reset = () => {
    const id = batchId;
    startMutation.reset();
    if (id) {
      queryClient.removeQueries({ queryKey: ['batch-clone-status', id] });
    }
  };

  const downloadZipUrl = batchId && status === 'complete' ? apiClient.getBatchCloneZipUrl(batchId) : null;

  return {
    startBatchClone: startMutation.mutateAsync,
    isStarting: startMutation.isPending,
    batchId,
    status,
    progress,
    error,
    downloadZipUrl,
    reset,
  };
}

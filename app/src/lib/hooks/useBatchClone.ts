import { useEffect, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/lib/api/client';
import {
  clearCachedBatchZip,
  clearStoredBatch,
  getStoredBatchId,
  getStoredBatchStatus,
  setStoredBatchId,
  setStoredBatchStatus,
} from '@/lib/utils/batchCloneStorage';

const POLL_INTERVAL_MS = 1500;

export function useBatchClone() {
  const queryClient = useQueryClient();
  const onlineListenerRef = useRef<(() => void) | null>(null);

  const startMutation = useMutation({
    mutationFn: (formData: FormData) => apiClient.startBatchClone(formData),
    onSuccess: (data) => {
      if (data?.batch_id) {
        setStoredBatchId(data.batch_id);
      }
    },
  });

  const stopMutation = useMutation({
    mutationFn: (id: string) => apiClient.stopBatchClone(id),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: ['batch-clone-status', id] });
    },
  });

  const batchIdFromMutation = startMutation.data?.batch_id;
  const batchIdFromStorage = getStoredBatchId();
  const batchId = batchIdFromMutation ?? batchIdFromStorage ?? null;

  const statusQuery = useQuery({
    queryKey: ['batch-clone-status', batchId],
    queryFn: () => apiClient.getBatchCloneStatus(batchId!),
    enabled: !!batchId,
    refetchOnReconnect: true,
    retry: 5,
    retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 15000),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === 'complete' || status === 'error' || status === 'stopped') {
        return false;
      }
      return POLL_INTERVAL_MS;
    },
  });

  const status = statusQuery.data?.status;
  const progress = statusQuery.data?.progress;
  const error = statusQuery.data?.error;
  const logs = statusQuery.data?.logs;
  const workerStats = statusQuery.data?.worker_stats;

  useEffect(() => {
    if (status === 'complete' || status === 'error' || status === 'stopped') {
      setStoredBatchStatus(status);
    }
  }, [status]);

  useEffect(() => {
    if (!batchId || status === 'complete' || status === 'error' || status === 'stopped') {
      if (onlineListenerRef.current) {
        window.removeEventListener('online', onlineListenerRef.current);
        onlineListenerRef.current = null;
      }
      return;
    }
    const refetch = () => statusQuery.refetch();
    window.addEventListener('online', refetch);
    onlineListenerRef.current = refetch;
    return () => {
      window.removeEventListener('online', refetch);
      onlineListenerRef.current = null;
    };
  }, [batchId, status]);

  const reset = () => {
    const id = batchId;
    startMutation.reset();
    if (id) {
      queryClient.removeQueries({ queryKey: ['batch-clone-status', id] });
      clearCachedBatchZip(id).catch(() => {});
    }
    clearStoredBatch();
  };

  const lastKnownStatus = getStoredBatchStatus();
  const isRestoredBatch = !!batchId && !batchIdFromMutation;
  const isComplete = status === 'complete' || lastKnownStatus === 'complete';
  const downloadZipUrl = batchId && isComplete ? apiClient.getBatchCloneZipUrl(batchId) : null;

  return {
    startBatchClone: startMutation.mutateAsync,
    isStarting: startMutation.isPending,
    stopBatchClone: (id: string) => stopMutation.mutateAsync(id),
    isStopping: stopMutation.isPending,
    batchId,
    status,
    progress,
    error,
    logs,
    workerStats,
    downloadZipUrl,
    reset,
    isStatusError: statusQuery.isError,
    refetchStatus: () => statusQuery.refetch(),
    isRefetchingStatus: statusQuery.isFetching,
    lastKnownStatus,
    isRestoredBatch,
  };
}

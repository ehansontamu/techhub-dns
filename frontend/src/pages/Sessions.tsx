/**
 * Sessions Page
 *
 * Allows users to view and manage their active login sessions.
 */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Monitor } from 'lucide-react';

import { useAuth } from '../contexts/AuthContext';
import {
    getSessionsQueryOptions,
    revokeAllOtherSessions as revokeAllOtherSessionsRequest,
    revokeSession as revokeSessionRequest,
    sessionsQueryKeys,
} from '../queries/sessions';
import { formatToCentralTime } from '../utils/timezone';
import { getUserDisplayName } from '../utils/userDisplay';

export default function Sessions() {
    const { user, logout } = useAuth();
    const [error, setError] = useState<string | null>(null);
    const currentUserLabel = getUserDisplayName(user, "your account");
    const queryClient = useQueryClient();

    const sessionsQuery = useQuery({
        ...getSessionsQueryOptions(),
        retry: false,
    });

    const sessions = sessionsQuery.data ?? [];
    const isLoading = sessionsQuery.isPending;

    const revokeSessionMutation = useMutation({
        mutationFn: revokeSessionRequest,
        onSuccess: async () => {
            setError(null);
            await queryClient.invalidateQueries({ queryKey: sessionsQueryKeys.all });
        },
        onError: (err) => {
            setError('Failed to revoke session');
            console.error('Failed to revoke session:', err);
        },
    });

    const revokeAllSessionsMutation = useMutation({
        mutationFn: revokeAllOtherSessionsRequest,
        onSuccess: async () => {
            setError(null);
            await queryClient.invalidateQueries({ queryKey: sessionsQueryKeys.all });
        },
        onError: (err) => {
            setError('Failed to revoke sessions');
            console.error('Failed to revoke sessions:', err);
        },
    });

    const revokeSession = async (sessionId: string) => {
        await revokeSessionMutation.mutateAsync(sessionId);
    };

    const revokeAllOtherSessions = async () => {
        await revokeAllSessionsMutation.mutateAsync();
    };

    const formatDate = (dateString: string) => {
        return formatToCentralTime(dateString);
    };

    const parseUserAgent = (ua: string | null): string => {
        if (!ua) return 'Unknown device';
        if (ua.includes('iPhone') || ua.includes('iPad')) return 'iOS Device';
        if (ua.includes('Android')) return 'Android Device';
        if (ua.includes('Windows')) return 'Windows PC';
        if (ua.includes('Mac')) return 'Mac';
        if (ua.includes('Linux')) return 'Linux';
        return 'Unknown device';
    };

    if (isLoading) {
        return (
            <div className="flex items-center justify-center min-h-[400px]">
                <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary"></div>
            </div>
        );
    }

    const isMutating = revokeSessionMutation.isPending || revokeAllSessionsMutation.isPending;
    const effectiveError = error ?? (sessionsQuery.isError ? 'Failed to load sessions' : null);

    return (
        <div className="container mx-auto px-4 py-6 sm:px-6">
            <div className="mb-6 sm:mb-8">
                <h1 className="text-xl font-semibold tracking-tight text-foreground sm:text-2xl">Active Sessions</h1>
                <p className="text-sm text-muted-foreground mt-1">
                    Logged in as <strong className="text-foreground">{currentUserLabel}</strong>
                </p>
            </div>

            {effectiveError && (
                <div className="mb-4 p-4 bg-destructive/10 text-destructive rounded-lg text-sm">
                    {effectiveError}
                </div>
            )}

            <div className="rounded-lg border border-border bg-card divide-y divide-border">
                {sessions?.map((session) => (
                    <div key={session.id} className="p-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                        <div className="flex items-center gap-3 min-w-0">
                            <div className="h-10 w-10 shrink-0 bg-muted rounded-full flex items-center justify-center">
                                <Monitor className="h-5 w-5 text-muted-foreground" />
                            </div>
                            <div className="min-w-0">
                                <div className="font-medium text-foreground flex flex-wrap items-center gap-2">
                                    {parseUserAgent(session.user_agent)}
                                    {session.is_current && (
                                        <span className="text-xs bg-primary/10 text-primary px-2 py-0.5 rounded-full">
                                            Current
                                        </span>
                                    )}
                                </div>
                                <div className="text-sm text-muted-foreground truncate">
                                    {session.ip_address || 'Unknown IP'} &middot; Last active {formatDate(session.last_seen_at)}
                                </div>
                            </div>
                        </div>

                        {!session.is_current && (
                            <button
                                onClick={() => revokeSession(session.id)}
                                disabled={isMutating}
                                className="min-h-[44px] px-3 text-sm font-medium text-destructive hover:text-destructive/80 shrink-0 self-start sm:self-auto"
                            >
                                Sign out
                            </button>
                        )}
                    </div>
                ))}
                {sessions.length === 0 && !isLoading && (
                    <div className="p-8 text-center text-muted-foreground">
                        <Monitor className="mx-auto h-12 w-12 text-muted-foreground/40 mb-3" />
                        <p className="text-sm font-medium">No active sessions found</p>
                        <p className="text-xs mt-1 text-muted-foreground/80">Sessions from other devices will appear here</p>
                    </div>
                )}
            </div>

            {sessions.length > 1 && (
                <div className="mt-6">
                    <button
                        onClick={revokeAllOtherSessions}
                        disabled={isMutating}
                        className="min-h-[44px] px-4 text-sm font-medium text-destructive hover:text-destructive/80"
                    >
                        Sign out of all other sessions
                    </button>
                </div>
            )}

            <div className="mt-8 pt-8 border-t border-border">
                <button
                    onClick={logout}
                    className="min-h-[44px] px-4 py-2 bg-destructive text-destructive-foreground rounded-lg hover:bg-destructive/90 transition-colors text-sm font-medium"
                >
                    Sign out of this device
                </button>
            </div>
        </div>
    );
}

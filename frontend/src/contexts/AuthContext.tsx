/**
 * Authentication Context and Provider
 *
 * Provides authentication state and functions throughout the app.
 */

import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import apiClient from '../api/client';

export interface User {
    id: string;
    email: string;
    display_name: string | null;
    department: string | null;
    created_at: string;
    last_login_at: string;
}

export interface Session {
    id: string;
    created_at: string;
    expires_at: string;
    last_seen_at: string;
    user_agent: string | null;
    ip_address: string | null;
    is_current: boolean;
}

interface AuthContextType {
    user: User | null;
    session: Session | null;
    isAuthenticated: boolean;
    isAdmin: boolean;
    isLoading: boolean;
    login: () => void;
    logout: () => Promise<void>;
    refreshAuth: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
    const [user, setUser] = useState<User | null>(null);
    const [session, setSession] = useState<Session | null>(null);
    const [isAdmin, setIsAdmin] = useState(false);
    const [isLoading, setIsLoading] = useState(true);

    const refreshAuth = async () => {
        try {
            // Auth endpoints are at /api/auth
            const response = await apiClient.get('/auth/me', { withCredentials: true });
            setUser(response.data.user);
            setSession(response.data.session);
            setIsAdmin(Boolean(response.data.is_admin));
        } catch (error) {
            // Not authenticated or error - clear state
            setUser(null);
            setSession(null);
            setIsAdmin(false);
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => {
        refreshAuth();
    }, []);

    const login = () => {
        const returnTo = (() => {
            if (typeof window === 'undefined') {
                return '/';
            }

            try {
                const storedReturnTo = window.sessionStorage.getItem('auth:returnTo') ?? '';
                if (storedReturnTo) {
                    return storedReturnTo;
                }
            } catch (_error) {
                // Ignore storage access failures and fall back to the current URL.
            }

            return `${window.location.pathname}${window.location.search}${window.location.hash}` || '/';
        })();

        // Redirect to the backend login endpoint.
        window.location.href = '/auth/login?next=' + encodeURIComponent(returnTo);
    };

    const logout = async () => {
        try {
            // Auth endpoints are at /api/auth
            await apiClient.post('/auth/logout', {}, { withCredentials: true });
        } catch (error) {
            console.error('Logout error:', error);
        } finally {
            setUser(null);
            setSession(null);
            setIsAdmin(false);
            // Redirect to login page
            window.location.href = '/login';
        }
    };

    const value: AuthContextType = {
        user,
        session,
        isAuthenticated: !!user,
        isAdmin,
        isLoading,
        login,
        logout,
        refreshAuth,
    };

    return (
        <AuthContext.Provider value={value}>
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth(): AuthContextType {
    const context = useContext(AuthContext);
    if (context === undefined) {
        throw new Error('useAuth must be used within an AuthProvider');
    }
    return context;
}

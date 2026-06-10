/**
 * Login Page
 *
 * Minimal TAMU-branded login page with SSO button.
 */

import { useAuth } from '../contexts/AuthContext';
import { useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import boxTAM from '../../assets/boxTAM.svg';

export default function Login() {
    const { isAuthenticated, isLoading, login } = useAuth();
    const navigate = useNavigate();
    const location = useLocation();
    const errorCode = new URLSearchParams(location.search).get("error");
    const errorMessage =
        errorCode === "not_authorized"
            ? "Your TAMU account signed in successfully, but it is not approved for TechHub access. Please contact a TechHub administrator."
            : null;

    // Redirect if already authenticated
    useEffect(() => {
        if (!isLoading && isAuthenticated) {
            const state = location.state as
                | {
                      from?: {
                          pathname?: string;
                          search?: string;
                          hash?: string;
                      };
                  }
                | null;

            const stateDestination = state?.from
                ? `${state.from.pathname ?? ''}${state.from.search ?? ''}${state.from.hash ?? ''}`
                : '';

            let storedDestination = '';
            if (typeof window !== 'undefined') {
                try {
                    storedDestination = window.sessionStorage.getItem('auth:returnTo') ?? '';
                    window.sessionStorage.removeItem('auth:returnTo');
                } catch (_error) {
                    storedDestination = '';
                }
            }

            const destination = stateDestination || storedDestination || '/';
            navigate(destination.startsWith('/') ? destination : '/', { replace: true });
        }
    }, [isAuthenticated, isLoading, navigate, location]);

    if (isLoading) {
        return (
            <div className="w-full min-h-[100dvh] flex items-center justify-center bg-background">
                <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-maroon-700"></div>
            </div>
        );
    }

    return (
        <div className="w-full min-h-[100dvh] flex items-center justify-center bg-background">
            <div className="text-center">
                {/* A&M Logo */}
                <img
                    src={boxTAM}
                    alt="Texas A&M University"
                    className="mx-auto h-24 w-auto mb-8"
                />

                {errorMessage ? (
                    <div className="mb-6 max-w-md rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                        {errorMessage}
                    </div>
                ) : null}

                <button
                    onClick={login}
                    className="px-8 py-3 text-lg font-medium rounded-lg text-white bg-maroon-700 hover:bg-maroon-800 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-maroon-500 transition-colors"
                >
                    Sign in with NetID
                </button>
            </div>
        </div>
    );
}

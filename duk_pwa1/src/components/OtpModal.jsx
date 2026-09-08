/**
 * OtpModal.jsx — DUK Bus Tracker PWA
 * OTP verification rendered as a bottom-sheet modal over the setup screen.
 * Slides up from the bottom, dismissible only after success.
 */
import React, { useState, useRef, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { verify, register, setApiToken } from '../api';
import { saveToken, saveUser } from '../storage';
import { useToast } from '../App';

const CODE_LENGTH = 6;

export default function OtpModal({ isOpen, email, name, boardingStop, onClose }) {
  const navigate  = useNavigate();
  const showToast = useToast();

  const [otp,         setOtp]         = useState('');
  const [error,       setError]       = useState('');
  const [resendTimer, setResendTimer] = useState(30);
  const [verifying,   setVerifying]   = useState(false);
  const [resending,   setResending]   = useState(false);
  const [shaking,     setShaking]     = useState(false);

  const inputRef = useRef(null);
  const overlayRef = useRef(null);

  // Reset state when modal opens
  useEffect(() => {
    if (isOpen) {
      setOtp('');
      setError('');
      setResendTimer(30);
      setTimeout(() => inputRef.current?.focus(), 350); // after animation
    }
  }, [isOpen]);

  // Countdown timer
  useEffect(() => {
    if (!isOpen || resendTimer <= 0) return;
    const id = setInterval(() => setResendTimer(t => t - 1), 1000);
    return () => clearInterval(id);
  }, [isOpen, resendTimer]);



  // Trap body scroll
  useEffect(() => {
    document.body.style.overflow = isOpen ? 'hidden' : '';
    return () => { document.body.style.overflow = ''; };
  }, [isOpen]);

  const triggerShake = () => {
    setShaking(true);
    setTimeout(() => setShaking(false), 500);
  };

  const submitOtp = useCallback(async (code) => {
    if (verifying || code.length !== CODE_LENGTH) return;
    setVerifying(true);
    setError('');
    try {
      const res = await verify(email, code);
      setApiToken(res.access_token);
      saveToken(res.access_token);
      saveUser(res.user);
      import('../firebase').then(m => m.requestAndSaveFcmToken()).catch(() => {});
      navigate('/route', { replace: true });
    } catch (err) {
      setError(err.message || 'Invalid code. Please try again.');
      triggerShake();
      setOtp('');
      setTimeout(() => inputRef.current?.focus(), 50);
    } finally {
      setVerifying(false);
    }
  }, [email, verifying]);

  const handleChange = (e) => {
    setError('');
    const val = e.target.value.replace(/[^0-9]/g, '').slice(0, CODE_LENGTH);
    setOtp(val);
    
    if (val.length === CODE_LENGTH) {
      setTimeout(() => submitOtp(val), 80);
    }
  };

  const handleResend = async () => {
    if (resendTimer > 0 || resending) return;
    setResending(true);
    try {
      await register(name, email, boardingStop?.id);
      setResendTimer(30);
      setOtp('');
      showToast('OTP resent to your email!', 'success');
      setTimeout(() => inputRef.current?.focus(), 100);
    } catch {
      showToast('Failed to resend. Try again.', 'error');
    } finally {
      setResending(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div
      className="otp-overlay"
      ref={overlayRef}
      role="dialog"
      aria-modal="true"
      aria-label="Verify your email"
    >
      {/* Backdrop */}
      <div className="otp-backdrop" />

      {/* Bottom sheet */}
      <div className={`otp-sheet ${isOpen ? 'otp-sheet--open' : ''}`}>

        {/* Handle bar */}
        <div className="otp-sheet__handle" />

        {/* Header */}
        <div className="otp-sheet__header">
          <div>
            <div className="otp-sheet__title">Check your email</div>
            <div className="otp-sheet__sub">
              We sent a 6-digit code to<br />
              <strong>{email}</strong>
            </div>
          </div>
        </div>

        <div className={`otp-single-container${shaking ? ' otp-shake' : ''}`}>
          <input
            id="otp-single"
            ref={inputRef}
            className={`otp-single-input${error ? ' otp-input--error' : ''}`}
            type="tel"
            inputMode="numeric"
            pattern="[0-9]*"
            maxLength={CODE_LENGTH}
            value={otp}
            onChange={handleChange}
            autoComplete="one-time-code"
            disabled={verifying}
            placeholder="000000"
          />
        </div>

        {/* Error */}
        {error && (
          <p className="otp-error-msg">{error}</p>
        )}

        {/* Verify button */}
        <button
          id="verify-btn"
          className="setup-submit-btn"
          onClick={() => submitOtp(otp)}
          disabled={otp.length < CODE_LENGTH || verifying}
        >
          {verifying ? 'Verifying…' : 'Verify & Continue →'}
        </button>

        {/* Resend */}
        <div className="otp-resend">
          {resendTimer > 0 ? (
            <>Resend code in <strong>{resendTimer}s</strong></>
          ) : (
            <span
              className="otp-resend__link"
              role="button"
              tabIndex={0}
              onClick={handleResend}
              onKeyDown={e => e.key === 'Enter' && handleResend()}
            >
              {resending ? 'Sending…' : 'Resend OTP'}
            </span>
          )}
        </div>

        {/* Change email */}
        <button className="otp-change-email" onClick={onClose}>
          ← Use a different email
        </button>
      </div>
    </div>
  );
}

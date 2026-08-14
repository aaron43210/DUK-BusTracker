import React, { useState } from 'react';
import { X, Send } from 'lucide-react';
import { submitSuggestion } from '../api';
import { useToast } from '../App';

export default function SuggestionsModal({ isOpen, onClose }) {
  const [suggestion, setSuggestion] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const showToast = useToast();

  if (!isOpen) return null;

  const handleSubmit = async () => {
    if (!suggestion.trim()) return;
    setSubmitting(true);
    try {
      await submitSuggestion(suggestion.trim(), '', '');
      setSuggestion('');
      onClose();
      showToast('Suggestion submitted! Thank you ', 'success');
    } catch (err) {
      showToast('Failed to send suggestion. Try again.', 'error');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={e => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose} aria-label="Close">
          <X size={20} />
        </button>
        <h2 className="modal-title">Send Feedback</h2>
        <p className="modal-subtitle">Share your thoughts, issues, or ideas below.</p>

        <textarea
          className="modal-textarea"
          placeholder="I think the app could..."
          maxLength={500}
          value={suggestion}
          onChange={e => setSuggestion(e.target.value)}
          rows={5}
        />

        <div className="modal-footer">
          <span className="modal-count">{suggestion.length}/500</span>
          <button
            className="btn btn--primary modal-submit"
            onClick={handleSubmit}
            disabled={submitting || !suggestion.trim()}
          >
            {submitting ? 'Sending…' : <><Send size={16} /> Send</>}
          </button>
        </div>
      </div>
    </div>
  );
}

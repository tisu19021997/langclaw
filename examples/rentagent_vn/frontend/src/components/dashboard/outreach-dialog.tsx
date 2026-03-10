"use client";

import { useState, useEffect } from "react";
import { Send, Loader2, AlertCircle, MessageCircle } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useZaloStore } from "@/stores/zalo-store";
import * as api from "@/lib/api";
import type { Listing, OutreachMessage } from "@/types";

interface OutreachDialogProps {
  open: boolean;
  onClose: () => void;
  listing: Listing;
  campaignId: string;
  onZaloSettingsOpen: () => void;
  onSuccess?: () => void;
}

export function OutreachDialog({
  open,
  onClose,
  listing,
  campaignId,
  onZaloSettingsOpen,
  onSuccess,
}: OutreachDialogProps) {
  const { status, fetchStatus, connecting } = useZaloStore();

  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [outreach, setOutreach] = useState<OutreachMessage | null>(null);
  const [editedText, setEditedText] = useState("");
  const [statusChecked, setStatusChecked] = useState(false);

  const isConnected = status?.connected ?? false;

  // Check Zalo status when dialog opens
  useEffect(() => {
    if (open) {
      setError(null);
      setOutreach(null);
      setEditedText("");
      setStatusChecked(false);

      fetchStatus().then(() => {
        setStatusChecked(true);
      });
    }
  }, [open, fetchStatus]);

  // Load draft after status is confirmed connected
  useEffect(() => {
    if (open && statusChecked && isConnected && !outreach && !loading) {
      loadOrCreateDraft();
    }
  }, [open, statusChecked, isConnected]);

  const loadOrCreateDraft = async () => {
    setLoading(true);
    setError(null);
    try {
      // First check for existing draft
      const history = await api.getOutreachHistory(campaignId, listing.id);
      const existingDraft = history.find((m) => m.status === "drafted");

      if (existingDraft) {
        // Use existing draft
        setOutreach(existingDraft);
        setEditedText(existingDraft.draft_text);
      } else {
        // Create new draft via LLM
        const message = await api.draftOutreach(campaignId, listing.id);
        setOutreach(message);
        setEditedText(message.draft_text);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const handleSend = async () => {
    if (!outreach) return;

    setSending(true);
    setError(null);
    try {
      const finalText = editedText !== outreach.draft_text ? editedText : undefined;
      await api.sendOutreach(campaignId, listing.id, outreach.id, finalText);
      onSuccess?.();
      onClose();
    } catch (e) {
      const errorMessage = (e as Error).message;
      if (errorMessage.includes("ZALO_NOT_CONNECTED")) {
        setError("Zalo session expired. Please reconnect.");
      } else {
        setError(errorMessage);
      }
    } finally {
      setSending(false);
    }
  };

  const handleConnectClick = () => {
    onClose();
    onZaloSettingsOpen();
  };

  return (
    <Dialog open={open} onOpenChange={() => onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Send className="h-5 w-5" />
            Contact landlord
          </DialogTitle>
          {listing.landlord_phone && (
            <DialogDescription>
              Send Zalo message to {listing.landlord_name || listing.landlord_phone}
            </DialogDescription>
          )}
        </DialogHeader>

        <div className="space-y-4 py-2">
          {!statusChecked || connecting ? (
            <div className="flex flex-col items-center justify-center py-8 gap-3">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
              <p className="text-sm text-muted-foreground">
                Checking Zalo connection...
              </p>
            </div>
          ) : !isConnected ? (
            <div className="space-y-4">
              <Alert>
                <MessageCircle className="h-4 w-4" />
                <AlertDescription>
                  You need to connect your Zalo account to send messages to landlords.
                </AlertDescription>
              </Alert>
              <Button onClick={handleConnectClick} className="w-full">
                Connect Zalo
              </Button>
            </div>
          ) : loading ? (
            <div className="flex flex-col items-center justify-center py-8 gap-3">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
              <p className="text-sm text-muted-foreground">
                Drafting message...
              </p>
            </div>
          ) : error ? (
            <div className="space-y-4">
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
              <Button onClick={loadOrCreateDraft} variant="outline" className="w-full">
                Retry
              </Button>
            </div>
          ) : outreach ? (
            <div className="space-y-3">
              <div className="rounded-lg bg-muted/50 p-3">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xs font-medium">Send to:</span>
                  <span className="text-xs text-muted-foreground">
                    {listing.landlord_phone}
                  </span>
                </div>
                {listing.title && (
                  <p className="text-xs text-muted-foreground line-clamp-1">
                    {listing.title}
                  </p>
                )}
              </div>

              <div className="space-y-1.5">
                <label className="text-sm font-medium">Message content</label>
                <Textarea
                  value={editedText}
                  onChange={(e) => setEditedText(e.target.value)}
                  rows={4}
                  className="resize-none"
                  placeholder="Message content..."
                />
                <p className="text-xs text-muted-foreground">
                  You can edit the message before sending
                </p>
              </div>
            </div>
          ) : null}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={sending}>
            Cancel
          </Button>
          {isConnected && outreach && (
            <Button
              onClick={handleSend}
              disabled={sending || !editedText.trim()}
            >
              {sending ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Sending...
                </>
              ) : (
                <>
                  <Send className="h-4 w-4 mr-2" />
                  Send
                </>
              )}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

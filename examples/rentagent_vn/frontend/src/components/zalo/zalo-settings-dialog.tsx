"use client";

import { useState, useEffect } from "react";
import { MessageCircle, Wifi, WifiOff, AlertCircle } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useZaloStore } from "@/stores/zalo-store";

interface ZaloSettingsDialogProps {
  open: boolean;
  onClose: () => void;
}

export function ZaloSettingsDialog({ open, onClose }: ZaloSettingsDialogProps) {
  const { status, connecting, error, fetchStatus, connectCookie, disconnect, clearError } =
    useZaloStore();

  const [cookie, setCookie] = useState("");
  const [imei, setImei] = useState("");
  const [userAgent, setUserAgent] = useState("");

  useEffect(() => {
    if (open) {
      fetchStatus();
      if (typeof navigator !== "undefined") {
        setUserAgent(navigator.userAgent);
      }
    }
  }, [open, fetchStatus]);

  const handleConnect = async () => {
    if (!cookie.trim() || !imei.trim() || !userAgent.trim()) return;
    try {
      await connectCookie(cookie, imei, userAgent);
      setCookie("");
      setImei("");
    } catch {
      // Error handled in store
    }
  };

  const handleDisconnect = async () => {
    await disconnect();
  };

  const isConnected = status?.connected ?? false;

  return (
    <Dialog open={open} onOpenChange={() => onClose()}>
      <DialogContent className="sm:max-w-md max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <MessageCircle className="h-5 w-5" />
            Cài đặt Zalo
          </DialogTitle>
          <DialogDescription>
            Kết nối tài khoản Zalo để gửi tin nhắn cho chủ nhà trực tiếp từ ứng dụng.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Connection Status */}
          <div className="flex items-center gap-3 p-3 rounded-lg border">
            {isConnected ? (
              <Wifi className="h-5 w-5 text-green-500" />
            ) : (
              <WifiOff className="h-5 w-5 text-muted-foreground" />
            )}
            <div className="flex-1">
              <p className="text-sm font-medium">
                {isConnected ? "Đã kết nối" : "Chưa kết nối"}
              </p>
              {isConnected && status?.phone_number && (
                <p className="text-xs text-muted-foreground">
                  SĐT: {status.phone_number}
                </p>
              )}
            </div>
            {isConnected && (
              <span className="h-2 w-2 rounded-full bg-green-500" />
            )}
          </div>

          {error && (
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          {isConnected ? (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Tài khoản Zalo của bạn đã được kết nối. Bạn có thể gửi tin nhắn cho chủ nhà từ chi tiết tin đăng.
              </p>
              <Button
                variant="outline"
                onClick={handleDisconnect}
                disabled={connecting}
                className="w-full"
              >
                {connecting ? "Đang ngắt kết nối..." : "Ngắt kết nối"}
              </Button>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="space-y-3">
                <div className="space-y-1.5">
                  <Label htmlFor="cookie">Cookie Zalo</Label>
                  <Textarea
                    id="cookie"
                    value={cookie}
                    onChange={(e) => setCookie(e.target.value)}
                    placeholder='{"zpw_sek": "...", "zpw_uid": "...", ...}'
                    className="font-mono text-xs h-[100px] min-h-[100px] max-h-[100px] overflow-auto resize-none break-all"
                  />
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="imei">IMEI</Label>
                  <Input
                    id="imei"
                    value={imei}
                    onChange={(e) => setImei(e.target.value)}
                    placeholder="Nhập IMEI từ DevTools"
                    className="font-mono text-xs"
                  />
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="userAgent">User Agent</Label>
                  <Input
                    id="userAgent"
                    value={userAgent}
                    onChange={(e) => setUserAgent(e.target.value)}
                    placeholder="Mozilla/5.0..."
                    className="font-mono text-xs"
                  />
                </div>
              </div>

              <div className="rounded-lg bg-muted/50 p-3">
                <p className="text-xs font-medium mb-2">Cách lấy thông tin đăng nhập:</p>
                <ol className="text-xs text-muted-foreground space-y-1 list-decimal list-inside">
                  <li>Mở chat.zalo.me trên Chrome</li>
                  <li>Nhấn F12 để mở DevTools</li>
                  <li>Chọn tab Application → Cookies</li>
                  <li>Copy tất cả cookies dạng JSON</li>
                  <li>IMEI có thể lấy từ Network tab khi gửi tin nhắn</li>
                </ol>
              </div>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            {isConnected ? "Đóng" : "Hủy"}
          </Button>
          {!isConnected && (
            <Button
              onClick={handleConnect}
              disabled={connecting || !cookie.trim() || !imei.trim()}
            >
              {connecting ? "Đang kết nối..." : "Kết nối"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

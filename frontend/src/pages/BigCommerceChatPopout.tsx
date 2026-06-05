import BigCommerceChat from './BigCommerceChat';

export default function BigCommerceChatPopout() {
  return (
    <div className="h-screen w-screen min-h-[520px] bg-background flex items-center justify-center">
      <div className="w-full max-w-xl">
        <BigCommerceChat />
      </div>
    </div>
  );
}

import { useCallback, useRef, type KeyboardEvent, type PointerEvent } from "react";

import { Button } from "../ui/button";
import { X } from "lucide-react";

type Placement = {
  id: string;
  pageIndex: number;
  x: number;
  y: number;
  width: number;
  height: number;
  dataUrl: string;
};

type PlacementViewport = { width: number; height: number };

type InteractionState =
  | {
      id: string;
      mode: "drag";
      pointerId: number;
      startClientX: number;
      startClientY: number;
      initialPlacement: Placement;
    }
  | {
      id: string;
      mode: "resize";
      pointerId: number;
      startClientX: number;
      startClientY: number;
      initialPlacement: Placement;
      anchorX: number;
      anchorY: number;
      minSize: number;
      aspectRatio: number;
    };

type SignaturePlacementLayerProps = {
  placements: Placement[];
  pageViewport: PlacementViewport | null;
  scale: number;
  selectedPlacementId: string | null;
  onSelect: (id: string) => void;
  onRemove: (id: string) => void;
  onUpdatePlacement: (id: string, updater: (placement: Placement) => Placement) => void;
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>, placement: Placement) => void;
};

function getPlacementStyle(placement: Placement, pageViewport: PlacementViewport, scale: number) {
  const xPx = placement.x * scale;
  const yPx = (pageViewport.height - placement.y - placement.height) * scale;
  const widthPx = placement.width * scale;
  const heightPx = placement.height * scale;

  return {
    left: 0,
    top: 0,
    width: `${widthPx}px`,
    height: `${heightPx}px`,
    position: "absolute" as const,
    transform: `translate3d(${xPx}px, ${yPx}px, 0)`,
  };
}

const HANDLE_MIN_SIZE = 32;

function SignaturePlacementItem({
  placement,
  pageViewport,
  scale,
  isSelected,
  onSelect,
  onRemove,
  onUpdatePlacement,
  onKeyDown,
}: {
  placement: Placement;
  pageViewport: PlacementViewport;
  scale: number;
  isSelected: boolean;
  onSelect: (id: string) => void;
  onRemove: (id: string) => void;
  onUpdatePlacement: (id: string, updater: (placement: Placement) => Placement) => void;
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>, placement: Placement) => void;
}) {
  const interactionRef = useRef<InteractionState | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);

  const clearInteraction = useCallback(() => {
    interactionRef.current = null;
  }, []);

  const updateFromPointer = useCallback(
    (clientX: number, clientY: number) => {
      const interaction = interactionRef.current;
      if (!interaction) return;

      const deltaXPt = (clientX - interaction.startClientX) / scale;
      const deltaYPt = -(clientY - interaction.startClientY) / scale;

      if (interaction.mode === "drag") {
        const newX = interaction.initialPlacement.x + deltaXPt;
        const newY = interaction.initialPlacement.y + deltaYPt;
        console.log('[SIG_DRAG]', {id: interaction.id, mode: 'drag', startX: interaction.startClientX, startY: interaction.startClientY, clientX, clientY, deltaXPt, deltaYPt, newX, newY, scale});
        onUpdatePlacement(interaction.id, (current) => {
          console.log('[SIG_DRAG_UPDATER]', {id: interaction.id, currentX: current.x, currentY: current.y, newX, newY, changed: current.x !== newX || current.y !== newY});
          return {
            ...current,
            x: newX,
            y: newY,
          };
        });
        return;
      }

      const handleX = interaction.initialPlacement.x + deltaXPt;
      const handleY = interaction.initialPlacement.y + deltaYPt;

      let nextWidth = interaction.anchorX - handleX;
      let nextHeight = interaction.anchorY - handleY;

      if (Math.abs(deltaXPt) >= Math.abs(deltaYPt)) {
        nextWidth = interaction.anchorX - handleX;
        nextHeight = nextWidth / interaction.aspectRatio;
      } else {
        nextHeight = interaction.anchorY - handleY;
        nextWidth = nextHeight * interaction.aspectRatio;
      }

      if (nextWidth < interaction.minSize) {
        nextWidth = interaction.minSize;
        nextHeight = nextWidth / interaction.aspectRatio;
      }
      if (nextHeight < interaction.minSize) {
        nextHeight = interaction.minSize;
        nextWidth = nextHeight * interaction.aspectRatio;
      }

      console.log('RESIZE update:', {id: interaction.id, nextWidth, nextHeight, newX: interaction.anchorX - nextWidth, newY: interaction.anchorY - nextHeight});
      onUpdatePlacement(interaction.id, (current) => ({
        ...current,
        x: interaction.anchorX - nextWidth,
        y: interaction.anchorY - nextHeight,
        width: nextWidth,
        height: nextHeight,
      }));
    },
    [onUpdatePlacement, scale]
  );

  const beginDrag = useCallback((event: PointerEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement | null;
    if (target?.closest("[data-resize-handle], [data-delete-button]")) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    onSelect(placement.id);
    interactionRef.current = {
      id: placement.id,
      mode: "drag",
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      initialPlacement: placement,
    };
    rootRef.current?.setPointerCapture(event.pointerId);
  }, [onSelect, placement]);

  const beginResize = useCallback((event: PointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    onSelect(placement.id);
    interactionRef.current = {
      id: placement.id,
      mode: "resize",
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      initialPlacement: placement,
      anchorX: placement.x + placement.width,
      anchorY: placement.y + placement.height,
      minSize: Math.max(HANDLE_MIN_SIZE, Math.min(placement.width, placement.height) * 0.4),
      aspectRatio: placement.width / Math.max(placement.height, 0.1),
    };
    rootRef.current?.setPointerCapture(event.pointerId);
  }, [onSelect, placement]);

  return (
    <div
      ref={rootRef}
      role="button"
      tabIndex={0}
      aria-label="Signature placement"
      className={`group z-20 cursor-move select-none pointer-events-auto focus-visible:outline focus-visible:outline-[3px] focus-visible:outline-ring focus-visible:outline-offset-2 ${isSelected ? "ring-2 ring-ring ring-offset-2 ring-offset-background" : ""}`}
      style={{
        ...getPlacementStyle(placement, pageViewport, scale),
        touchAction: "none",
      }}
      onPointerDown={beginDrag}
      onPointerMove={(event) => {
        if (!interactionRef.current || interactionRef.current.pointerId !== event.pointerId) return;
        event.preventDefault();
        updateFromPointer(event.clientX, event.clientY);
      }}
      onPointerUp={(event) => {
        if (!interactionRef.current || interactionRef.current.pointerId !== event.pointerId) return;
        event.preventDefault();
        clearInteraction();
        if (rootRef.current?.hasPointerCapture(event.pointerId)) {
          rootRef.current.releasePointerCapture(event.pointerId);
        }
      }}
      onPointerCancel={(event) => {
        if (!interactionRef.current || interactionRef.current.pointerId !== event.pointerId) return;
        clearInteraction();
        if (rootRef.current?.hasPointerCapture(event.pointerId)) {
          rootRef.current.releasePointerCapture(event.pointerId);
        }
      }}
      onKeyDown={(event) => onKeyDown(event, placement)}
    >
      <img
        src={placement.dataUrl}
        alt="Signature"
        className="h-full w-full object-contain pointer-events-none"
        draggable={false}
      />

      {isSelected && (
        <Button
          type="button"
          data-delete-button
          variant="destructive"
          size="icon"
          aria-label="Remove signature"
          className="pointer-events-auto absolute -right-3 -top-3 z-20 h-9 w-9 rounded-full p-0 shadow-premium focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-destructive"
          onPointerDown={(event) => {
            event.stopPropagation();
          }}
          onClick={(event) => {
            event.stopPropagation();
            onRemove(placement.id);
          }}
        >
          <X className="h-4 w-4" />
        </Button>
      )}

      {isSelected && (
        <button
          type="button"
          aria-label="Resize signature"
          data-resize-handle
          className="pointer-events-auto absolute -bottom-4 -left-4 z-20 flex h-10 w-10 cursor-nw-resize items-center justify-center rounded-full border-2 border-primary bg-background shadow-premium transition-transform hover:scale-105 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          onPointerDown={beginResize}
          style={{ touchAction: "none" }}
        >
          <svg className="h-4 w-4 text-primary" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M18 6L6 18" />
            <path d="M12 6h6v6" />
          </svg>
        </button>
      )}
    </div>
  );
}

export function SignaturePlacementLayer({
  placements,
  pageViewport,
  scale,
  selectedPlacementId,
  onSelect,
  onRemove,
  onUpdatePlacement,
  onKeyDown,
}: SignaturePlacementLayerProps) {
  if (!pageViewport) {
    return null;
  }

  return (
    <>
      {placements.map((placement) => (
        <SignaturePlacementItem
          key={placement.id}
          placement={placement}
          pageViewport={pageViewport}
          scale={scale}
          isSelected={selectedPlacementId === placement.id}
          onSelect={onSelect}
          onRemove={onRemove}
          onUpdatePlacement={onUpdatePlacement}
          onKeyDown={onKeyDown}
        />
      ))}
    </>
  );
}

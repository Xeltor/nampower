#pragma once

#include "types.h"
#include "logging.hpp"
#include <unordered_set>
#include <vector>

namespace Nampower {
    class CastQueue {
    private:
        int maxSize;
        std::vector<CastSpellParams> queue;
        int front;
        int rear;
        int size;
        std::unordered_set<uint32_t> unresolvedEvictionSpellIds;

    public:
        explicit CastQueue(int maxSize)
                : maxSize(maxSize), queue(maxSize), front(0), rear(-1), size(0) {}

        bool isFull() const {
            return size == maxSize;
        }

        bool isEmpty() const {
            return size == 0;
        }

        void clear() {
            front = 0;
            rear = -1;
            size = 0;
            unresolvedEvictionSpellIds.clear();
        }

        void pushFront(const CastSpellParams &params) {
            if (isFull()) {
                auto const &evicted = queue[(front + size - 1) % maxSize];
                if (evicted.castResult == CastResult::WAITING_FOR_SERVER) {
                    // Once an unresolved generation is lost, a later packet
                    // with this spell ID can never be mapped exactly again in
                    // this player context. Preserve a conservative tombstone.
                    unresolvedEvictionSpellIds.insert(evicted.spellId);
                }
                // Shift all elements one position to the right
                for (int i = size - 1; i > 0; --i) {
                    queue[(front + i) % maxSize] = queue[(front + i - 1) % maxSize];
                }
                queue[front] = params;
            } else {
                front = (front - 1 + maxSize) % maxSize;
                queue[front] = params;
                if (size == 0) {
                    rear = front;
                }
                size++;
            }
        }

        void push(const CastSpellParams &params, bool replaceMatchingNonGcdCategory) {
            if (replaceMatchingNonGcdCategory && params.castType == CastType::NON_GCD && params.gcdCategory != 0) {
                auto nonGcdParams = findGcdCategory(params.gcdCategory);
                if (nonGcdParams) {
                    DEBUG_LOG("Replacing queued nonGcd spell " << game::GetSpellName(nonGcdParams->spellId) << " with "
                                                               << game::GetSpellName(params.spellId)
                                                               << " for gcdCategory " << params.gcdCategory);
                    *nonGcdParams = params;
                    return;
                }
            }
            if (isFull()) {
                front = (front + 1) % maxSize;
            } else {
                size++;
            }
            rear = (rear + 1) % maxSize;
            queue[rear] = params;
        }

        CastSpellParams pop() {
            if (isEmpty()) {
                return CastSpellParams{};
            }
            CastSpellParams result = queue[front];
            front = (front + 1) % maxSize;
            size--;
            return result;
        }

        CastSpellParams *peek() {
            if (isEmpty()) {
                return nullptr;
            }
            return &queue[front];
        }

        CastSpellParams *findSpellIdWithMaxStartTime(uint32_t spellId, uint32_t maxStartTimeMs) {
            for (int i = 0; i < size; i++) {
                int index = (front + i) % maxSize;
                if (queue[index].spellId == spellId && queue[index].castStartTimeMs < maxStartTimeMs) {
                    return &queue[index];
                }
            }
            return nullptr;
        }

        CastSpellParams *findSpellId(uint32_t spellId) {
            for (int i = 0; i < size; i++) {
                int index = (front + i) % maxSize;
                if (queue[index].spellId == spellId) {
                    return &queue[index];
                }
            }
            return nullptr;
        }

        CastSpellParams *findCastId(uint64_t castId) {
            for (int i = 0; i < size; i++) {
                int index = (front + i) % maxSize;
                if (queue[index].castId == castId) {
                    return &queue[index];
                }
            }
            return nullptr;
        }

        bool removeSpellId(uint32_t spellId) {
            for (int i = 0; i < size; i++) {
                int index = (front + i) % maxSize;
                if (queue[index].spellId == spellId) {
                    // Shift all elements after this one forward
                    for (int j = i; j < size - 1; j++) {
                        int currentIndex = (front + j) % maxSize;
                        int nextIndex = (front + j + 1) % maxSize;
                        queue[currentIndex] = queue[nextIndex];
                    }
                    rear = (rear - 1 + maxSize) % maxSize;
                    size--;
                    return true;
                }
            }
            return false;
        }

        CastSpellParams *findOldestWaitingForServerSpellId(uint32_t spellId) {
            for (int i = size - 1; i >= 0; i--) {
                int index = (front + i) % maxSize;
                if (queue[index].spellId == spellId && queue[index].castResult == CastResult::WAITING_FOR_SERVER) {
                    return &queue[index];
                }
            }
            return nullptr;
        }

        CastSpellParams *findNewestWaitingForServerSpellId(uint32_t spellId) {
            for (int i = 0; i < size; i++) {
                int index = (front + i) % maxSize;
                if (queue[index].spellId == spellId && queue[index].castResult == CastResult::WAITING_FOR_SERVER) {
                    return &queue[index];
                }
            }
            return nullptr;
        }

        CastSpellParams *findUniqueWaitingForServerSpellId(uint32_t spellId) {
            if (unresolvedEvictionSpellIds.find(spellId)
                != unresolvedEvictionSpellIds.end()) {
                return nullptr;
            }
            CastSpellParams *match = nullptr;
            int matches = 0;
            for (int i = 0; i < size; i++) {
                int index = (front + i) % maxSize;
                if (queue[index].spellId == spellId
                    && queue[index].castResult == CastResult::WAITING_FOR_SERVER) {
                    matches++;
                    match = &queue[index];
                }
            }
            if (matches > 1) {
                for (int i = 0; i < size; i++) {
                    int index = (front + i) % maxSize;
                    if (queue[index].spellId == spellId
                        && queue[index].castResult == CastResult::WAITING_FOR_SERVER) {
                        queue[index].resultCorrelationAmbiguous = true;
                    }
                }
                return nullptr;
            }
            if (match && match->resultCorrelationAmbiguous) {
                return nullptr;
            }
            return match;
        }

        CastSpellParams *findNewestSuccessfulSpellId(uint32_t spellId) {
            for (int i = 0; i < size; i++) {
                int index = (front + i) % maxSize;
                if (queue[index].spellId == spellId && queue[index].castResult == CastResult::SERVER_SUCCESS) {
                    return &queue[index];
                }
            }
            return nullptr;
        }

        CastSpellParams *findGcdCategory(uint32_t gcdCategory) {
            for (int i = 0; i < size; i++) {
                int index = (front + i) % maxSize;
                if (queue[index].gcdCategory == gcdCategory) {
                    return &queue[index];
                }
            }
            return nullptr;
        }

        void logHistory() {
            for (int i = 0; i < size; i++) {
                int index = (front + i) % maxSize;
                DEBUG_LOG("Cast history " << i << ": " << game::GetSpellName(queue[index].spellId) << " result "
                                          << queue[index].castResult);
            }
        }

        int getSize() const {
            return size;
        }

        int getMaxSize() const {
            return maxSize;
        }
    };
}

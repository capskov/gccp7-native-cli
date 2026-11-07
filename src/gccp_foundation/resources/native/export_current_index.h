// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <QModelIndex>
#include <cstring>

// MSVC x64 member functions returning QModelIndex use a hidden result pointer
// after `this`. A free-function pointer returning QModelIndex has a different
// argument order, so it must never be used for this exported member getter.
class PinnedGlodonViewAbi {};
typedef QModelIndex (PinnedGlodonViewAbi::*PinnedCurrentMethod)() const;
static QModelIndex readPinnedCurrentIndex(const void *view, const void *address) {
    static_assert(sizeof(PinnedCurrentMethod)==sizeof(address),
                  "Requires the verified MSVC x64 single-inheritance ABI");
    if(!view || !address)return QModelIndex();
    PinnedCurrentMethod method;
    std::memcpy(&method,&address,sizeof(method));
    return (static_cast<const PinnedGlodonViewAbi*>(view)->*method)();
}

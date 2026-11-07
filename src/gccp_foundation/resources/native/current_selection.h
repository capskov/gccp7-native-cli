// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <QAbstractItemModel>
#include <QItemSelectionModel>
#include <QJsonArray>
#include <QJsonObject>
#include <QSet>

// Read the application selection directly. Never forward a selection or call
// setCurrentIndex. Row paths are ephemeral and meaningful only in this capture.
static QJsonObject currentIndexEvidence(QAbstractItemModel *model, const QModelIndex &current) {
    QJsonObject out{{"verified",false},{"application_selection_changed",false}};
    if (!model || !current.isValid() || current.model()!=model) {
        out["reason"]="no_current_index"; return out;
    }
    const int columns=model->columnCount(current.parent());
    if (columns<1 || columns>100 || current.column()<0 || current.column()>=columns) {
        out["reason"]="current_column_outside_bounds"; return out;
    }
    QString path; QSet<QModelIndex> seen;
    auto index=current;
    for (int depth=0; index.isValid(); ++depth) {
        if (depth>20 || seen.contains(index) || index.model()!=model
                || index.row()<0 || index.row()>=model->rowCount(index.parent())) {
            out["reason"]="current_ancestry_invalid_or_unbounded"; return out;
        }
        seen.insert(index); path="/"+QString::number(index.row())+path;
        index=index.parent();
    }
    QJsonArray values;
    for (int c=0;c<columns;++c) {
        const auto cell=model->index(current.row(),c,current.parent());
        if (!cell.isValid()) {out["reason"]="current_row_incomplete";return out;}
        values.append(model->data(cell,Qt::DisplayRole).toString());
    }
    out["verified"]=true; out["row_path_session_only"]=path;
    out["column_session_only"]=current.column(); out["values"]=values;
    out["model_session_id"]=QString::number(reinterpret_cast<quintptr>(model));
    return out;
}

static QJsonObject currentSelection(QAbstractItemModel *model, QItemSelectionModel *selection) {
    if (!selection || selection->model()!=model)
        return QJsonObject{{"verified",false},{"application_selection_changed",false},
                           {"reason","selection_model_missing_or_mismatched"}};
    const auto current=selection->currentIndex();
    auto out=currentIndexEvidence(model,current);
    if(!out["verified"].toBool())return out;
    if(selection->currentIndex()!=current || selection->model()!=model)
        return QJsonObject{{"verified",false},{"application_selection_changed",false},
                           {"reason","selection_changed_during_capture"}};
    out["selected"]=selection->isSelected(current);
    out["selection_session_id"]=QString::number(reinterpret_cast<quintptr>(selection));
    return out;
}

import React, { useState, useEffect, useCallback } from 'react';
import {
  fetchChannels,
  createChannel,
  updateChannel,
  deleteChannel,
  fetchChannelVideos,
  assignVideoToChannel,
  updateChannelVideo,
  removeVideoFromChannel,
  fetchOutputs,
  getMediaUrl,
} from '../services/api';
import { ConfirmModal } from './ConfirmModal';
import { Toast } from './Toast';
import { VideoModal } from './VideoModal';
import { ChannelOverlayEditor } from './ChannelOverlayEditor';
import {
  Tv2,
  Plus,
  Tag,
  Copy,
  Check,
  Play,
  Trash2,
  Edit3,
  Search,
  Sparkles,
  Video,
  X,
  FolderPlus,
  RefreshCw,
  Loader2,
} from 'lucide-react';

const PLATFORMS = [

  { id: 'tiktok', name: 'TikTok', color: 'text-rose-600 bg-rose-50 border-rose-200' },
  { id: 'youtube', name: 'YouTube Shorts', color: 'text-red-600 bg-red-50 border-red-200' },
  { id: 'facebook', name: 'Facebook Reels', color: 'text-blue-600 bg-blue-50 border-blue-200' },
  { id: 'douyin', name: 'Douyin', color: 'text-pink-600 bg-pink-50 border-pink-200' },
  { id: 'kuaishou', name: 'Kuaishou', color: 'text-amber-600 bg-amber-50 border-amber-200' },
  { id: 'instagram', name: 'Instagram Reels', color: 'text-purple-600 bg-purple-50 border-purple-200' },
  { id: 'xiaohongshu', name: 'Xiaohongshu', color: 'text-rose-700 bg-rose-50 border-rose-300' },
];

const COLOR_THEMES = [
  { id: 'blue', name: 'Xanh Lam', bg: 'bg-blue-500', ring: 'ring-blue-500' },
  { id: 'purple', name: 'Tím Neon', bg: 'bg-purple-500', ring: 'ring-purple-500' },
  { id: 'emerald', name: 'Xanh Ngọc', bg: 'bg-emerald-500', ring: 'ring-emerald-500' },
  { id: 'rose', name: 'Hồng Đỏ', bg: 'bg-rose-500', ring: 'ring-rose-500' },
  { id: 'amber', name: 'Vàng Cam', bg: 'bg-amber-500', ring: 'ring-amber-500' },
  { id: 'indigo', name: 'Chàm Indigo', bg: 'bg-indigo-500', ring: 'ring-indigo-500' },
];

export function ChannelManager() {
  const [channels, setChannels] = useState([]);
  const [selectedChannelId, setSelectedChannelId] = useState(null);
  const [videos, setVideos] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadingVideos, setLoadingVideos] = useState(false);
  const [toast, setToast] = useState(null);

  // Filter & Search States
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [tagFilter, setTagFilter] = useState('');

  // Modals
  const [showChannelModal, setShowChannelModal] = useState(false);
  const [editingChannel, setEditingChannel] = useState(null);
  const [deleteChannelTarget, setDeleteChannelTarget] = useState(null);

  const [showAssignModal, setShowAssignModal] = useState(false);
  const [availableOutputs, setAvailableOutputs] = useState([]);
  const [selectedOutput, setSelectedOutput] = useState(null);
  const [editingVideo, setEditingVideo] = useState(null);
  const [deleteVideoTarget, setDeleteVideoTarget] = useState(null);

  const [previewVideo, setPreviewVideo] = useState(null);
  const [copiedId, setCopiedId] = useState(null);

  // Form State for Channel Creation/Edit
  const [channelForm, setChannelForm] = useState({
    name: '',
    platform: 'tiktok',
    handle: '',
    tagsInput: '',
    description: '',
    color: 'blue',
  });

  // Form State for Video Assignment / Edit
  const [videoForm, setVideoForm] = useState({
    title: '',
    caption: '',
    tagsInput: '',
    publish_status: 'READY',
    notes: '',
  });

  // ---------------------------------------------------------------------------
  // Data Loading
  // ---------------------------------------------------------------------------

  const loadChannels = useCallback(async () => {
    try {
      const data = await fetchChannels();
      const list = data.channels || [];
      setChannels(list);
      if (list.length > 0) {
        setSelectedChannelId((prev) => {
          if (prev && list.some((c) => c.channel_id === prev)) return prev;
          return list[0].channel_id;
        });
      } else {
        setSelectedChannelId(null);
      }
    } catch (err) {
      console.error('Failed to load channels:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadChannelVideos = useCallback(async (channelId) => {
    if (!channelId) {
      setVideos([]);
      return;
    }
    setLoadingVideos(true);
    try {
      const data = await fetchChannelVideos(channelId);
      setVideos(data.videos || []);
    } catch (err) {
      console.error('Failed to load channel videos:', err);
      setVideos([]);
    } finally {
      setLoadingVideos(false);
    }
  }, []);

  useEffect(() => {
    loadChannels();
  }, [loadChannels]);

  useEffect(() => {
    if (selectedChannelId) {
      loadChannelVideos(selectedChannelId);
    } else {
      setVideos([]);
    }
  }, [selectedChannelId, loadChannelVideos]);

  const activeChannel = channels.find((c) => c.channel_id === selectedChannelId);

  // ---------------------------------------------------------------------------
  // Channel CRUD Handlers
  // ---------------------------------------------------------------------------

  const handleOpenCreateChannel = () => {
    setEditingChannel(null);
    setChannelForm({
      name: '',
      platform: 'tiktok',
      handle: '',
      tagsInput: '',
      description: '',
      color: 'blue',
    });
    setShowChannelModal(true);
  };

  const handleOpenEditChannel = (chan, e) => {
    e.stopPropagation();
    setEditingChannel(chan);
    setChannelForm({
      name: chan.name || '',
      platform: chan.platform || 'tiktok',
      handle: chan.handle || '',
      tagsInput: (chan.tags || []).join(', '),
      description: chan.description || '',
      color: chan.color || 'blue',
    });
    setShowChannelModal(true);
  };

  const handleSaveChannel = async (e) => {
    e.preventDefault();
    if (!channelForm.name.trim()) return;

    const tags = channelForm.tagsInput
      .split(',')
      .map((t) => t.trim())
      .filter((t) => t.length > 0);

    const payload = {
      name: channelForm.name.trim(),
      platform: channelForm.platform,
      handle: channelForm.handle.trim(),
      tags: tags,
      description: channelForm.description.trim(),
      color: channelForm.color,
    };

    try {
      if (editingChannel) {
        await updateChannel(editingChannel.channel_id, payload);
        setToast({ type: 'success', title: 'Thành Công', message: 'Đã cập nhật thông tin kênh.' });
      } else {
        const res = await createChannel(payload);
        setToast({ type: 'success', title: 'Thành Công', message: 'Đã tạo kênh phân phối mới.' });
        if (res.channel_id) setSelectedChannelId(res.channel_id);
      }
      setShowChannelModal(false);
      loadChannels();
    } catch (err) {
      setToast({ type: 'error', title: 'Lỗi', message: err.message || 'Thao tác kênh thất bại' });
    }
  };

  const handleDeleteChannelConfirm = async () => {
    if (!deleteChannelTarget) return;
    try {
      await deleteChannel(deleteChannelTarget.channel_id);
      setToast({ type: 'success', title: 'Đã Xóa', message: 'Đã xóa kênh khỏi hệ thống.' });
      setDeleteChannelTarget(null);
      loadChannels();
    } catch (err) {
      setToast({ type: 'error', title: 'Lỗi', message: err.message || 'Xóa kênh thất bại' });
    }
  };

  // ---------------------------------------------------------------------------
  // Video Assignment & Content Handlers
  // ---------------------------------------------------------------------------

  const handleOpenAssignModal = async () => {
    try {
      const outData = await fetchOutputs();
      const outs = outData.outputs || [];
      setAvailableOutputs(outs);
      if (outs.length > 0) {
        setSelectedOutput(outs[0]);
        setVideoForm({
          title: `Video Reup #${outs[0].job_id.slice(-6)}`,
          caption: `#viral #trending #reup #${activeChannel?.platform || 'tiktok'}`,
          tagsInput: (activeChannel?.tags || []).join(', '),
          publish_status: 'READY',
          notes: '',
        });
      } else {
        setSelectedOutput(null);
      }
      setEditingVideo(null);
      setShowAssignModal(true);
    } catch (err) {
      console.error('Failed to load available outputs:', err);
      setToast({ type: 'error', title: 'Lỗi', message: 'Không thể nạp kho video thành phẩm.' });
    }

  };

  const handleOpenEditVideo = (vid) => {
    setEditingVideo(vid);
    setSelectedOutput({
      job_id: vid.job_id,
      output_path: vid.video_path,
    });
    setVideoForm({
      title: vid.title || '',
      caption: vid.caption || '',
      tagsInput: (vid.tags || []).join(', '),
      publish_status: vid.publish_status || 'DRAFT',
      notes: vid.notes || '',
    });
    setShowAssignModal(true);
  };

  const handleSaveVideoContent = async (e) => {
    e.preventDefault();
    if (!selectedChannelId) return;

    const tags = videoForm.tagsInput
      .split(',')
      .map((t) => t.trim())
      .filter((t) => t.length > 0);

    const payload = {
      title: videoForm.title.trim(),
      caption: videoForm.caption.trim(),
      tags: tags,
      publish_status: videoForm.publish_status,
      notes: videoForm.notes.trim(),
    };

    try {
      if (editingVideo) {
        await updateChannelVideo(editingVideo.id, payload);
        setToast({ type: 'success', title: 'Thành Công', message: 'Đã cập nhật nội dung video.' });
      } else {
        if (!selectedOutput) {
          setToast({ type: 'error', title: 'Lỗi', message: 'Vui lòng chọn 1 video từ kho thành phẩm.' });
          return;
        }
        await assignVideoToChannel(selectedChannelId, {
          ...payload,
          job_id: selectedOutput.job_id,
          video_path: selectedOutput.output_path || selectedOutput.output_file_path || '',
        });
        setToast({ type: 'success', title: 'Thành Công', message: 'Đã thêm video vào kênh quản lý.' });
      }
      setShowAssignModal(false);
      loadChannelVideos(selectedChannelId);
      loadChannels();
    } catch (err) {
      setToast({ type: 'error', title: 'Lỗi', message: err.message || 'Lưu video thất bại' });
    }
  };

  const handleQuickStatusChange = async (vid, newStatus) => {
    try {
      await updateChannelVideo(vid.id, { publish_status: newStatus });
      loadChannelVideos(selectedChannelId);
      loadChannels();
      setToast({
        type: 'success',
        title: 'Cập Nhật Trạng Thái',
        message: `Đã đổi trạng thái sang: ${
          newStatus === 'PUBLISHED' ? 'Đã Xuất Bản' : newStatus === 'READY' ? 'Sẵn Sàng Đăng' : 'Bản Nháp'
        }`,
      });
    } catch (err) {
      console.error('Failed to change status:', err);
      setToast({ type: 'error', title: 'Lỗi', message: 'Cập nhật trạng thái thất bại' });
    }
  };


  const handleDeleteVideoConfirm = async () => {
    if (!deleteVideoTarget) return;
    try {
      await removeVideoFromChannel(deleteVideoTarget.id);
      setToast({ type: 'success', title: 'Đã Xóa', message: 'Đã xóa video khỏi kênh.' });
      setDeleteVideoTarget(null);
      loadChannelVideos(selectedChannelId);
      loadChannels();
    } catch (err) {
      setToast({ type: 'error', title: 'Lỗi', message: err.message || 'Xóa video thất bại' });
    }
  };

  const handleCopyCaption = (vid) => {
    const text = `${vid.title || ''}\n\n${vid.caption || ''}\n${(vid.tags || []).map((t) => `#${t.replace(/\s+/g, '')}`).join(' ')}`;
    navigator.clipboard.writeText(text.trim());
    setCopiedId(vid.id);
    setTimeout(() => setCopiedId(null), 2000);
    setToast({ type: 'success', title: 'Đã Copy', message: 'Đã sao chép tiêu đề, caption & hashtags!' });
  };

  // ---------------------------------------------------------------------------
  // Filtered Video List
  // ---------------------------------------------------------------------------

  const filteredVideos = videos.filter((vid) => {
    if (statusFilter !== 'ALL' && vid.publish_status !== statusFilter) return false;
    if (tagFilter && !(vid.tags || []).includes(tagFilter)) return false;
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      const matchTitle = (vid.title || '').toLowerCase().includes(q);
      const matchCaption = (vid.caption || '').toLowerCase().includes(q);
      const matchTag = (vid.tags || []).some((t) => t.toLowerCase().includes(q));
      if (!matchTitle && !matchCaption && !matchTag) return false;
    }
    return true;
  });

  return (
    <div className="space-y-6">
      {/* Top Banner & Strategy Description */}
      <div className="bg-gradient-to-r from-blue-600 via-indigo-600 to-purple-600 rounded-3xl p-6 md:p-8 text-white shadow-xl flex flex-col md:flex-row items-start md:items-center justify-between gap-6 relative overflow-hidden">
        <div className="absolute -right-12 -bottom-12 w-64 h-64 bg-white/10 rounded-full blur-2xl pointer-events-none" />
        <div className="space-y-2 max-w-2xl relative z-10">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-white/20 backdrop-blur-md text-xs font-bold tracking-wide uppercase">
            <Sparkles className="w-3.5 h-3.5 text-amber-300" /> Hệ Thống Quản Trị Đa Kênh & Content Hub
          </div>
          <h2 className="text-2xl md:text-3xl font-extrabold tracking-tight">
            Quản Lý Kênh & Phân Loại Nội Dung Video
          </h2>
          <p className="text-xs md:text-sm text-blue-100/90 font-medium leading-relaxed">
            Gắn nhãn chủ đề, chuẩn bị tiêu đề, caption, hashtags và quản trị lịch đăng video cho từng kênh (TikTok, YouTube Shorts, FB Reels, Douyin) sẵn sàng cho tính năng tự động xuất bản đa nền tảng.
          </p>
        </div>
        <button
          onClick={handleOpenCreateChannel}
          className="px-5 py-3 bg-white hover:bg-slate-50 text-blue-700 font-extrabold text-xs md:text-sm rounded-2xl transition shadow-lg flex items-center gap-2 cursor-pointer shrink-0 active:scale-95"
        >
          <Plus className="w-4 h-4 stroke-[3]" /> Thêm Kênh Mới
        </button>
      </div>

      {/* Main Grid: Channels Sidebar (Left) + Content Manager (Right) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left Column: Channels List */}
        <div className="lg:col-span-4 space-y-4">
          <div className="bg-white rounded-2xl border border-slate-200/90 p-4 shadow-xs">
            <div className="flex items-center justify-between pb-3 border-b border-slate-100">
              <div className="flex items-center gap-2">
                <Tv2 className="w-4 h-4 text-blue-600" />
                <h3 className="text-xs font-extrabold uppercase tracking-wider text-slate-800">
                  Danh Sách Kênh ({channels.length})
                </h3>
              </div>
              <button
                onClick={loadChannels}
                className="p-1.5 text-slate-400 hover:text-blue-600 rounded-lg hover:bg-slate-100 transition"
                title="Tải lại danh sách kênh"
              >
                <RefreshCw className="w-3.5 h-3.5" />
              </button>
            </div>

            {/* Channels Card List */}
            <div className="space-y-2.5 mt-3 max-h-[600px] overflow-y-auto pr-1">
              {loading ? (
                <div className="text-center py-10 text-slate-400 text-xs flex items-center justify-center gap-2 font-medium">
                  <Loader2 className="w-4 h-4 animate-spin text-blue-600" /> Đang tải danh sách kênh...
                </div>
              ) : channels.length === 0 ? (
                <div className="text-center py-10 text-slate-400 text-xs">
                  <Tv2 className="w-8 h-8 text-slate-300 mx-auto mb-2" />
                  <p className="font-semibold text-slate-600">Chưa có kênh nào</p>
                  <p className="text-[11px] text-slate-400 mt-1">Bấm "Thêm Kênh Mới" để bắt đầu quản lý nội dung.</p>
                </div>
              ) : (
                channels.map((chan) => {
                  const isSelected = chan.channel_id === selectedChannelId;
                  const plat = PLATFORMS.find((p) => p.id === chan.platform) || PLATFORMS[0];

                  return (
                    <div
                      key={chan.channel_id}
                      onClick={() => setSelectedChannelId(chan.channel_id)}
                      className={`p-3.5 rounded-2xl border transition-all cursor-pointer relative group ${
                        isSelected
                          ? 'bg-blue-50/70 border-blue-500 shadow-sm ring-1 ring-blue-500/20'
                          : 'bg-slate-50/50 hover:bg-white border-slate-200/80 hover:border-slate-300'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex items-center gap-2.5 min-w-0">
                          <div className={`w-9 h-9 rounded-xl flex items-center justify-center font-black text-xs text-white shadow-xs ${
                            chan.color === 'purple' ? 'bg-purple-600' :
                            chan.color === 'emerald' ? 'bg-emerald-600' :
                            chan.color === 'rose' ? 'bg-rose-600' :
                            chan.color === 'amber' ? 'bg-amber-600' :
                            chan.color === 'indigo' ? 'bg-indigo-600' : 'bg-blue-600'
                          }`}>
                            {chan.name.slice(0, 2).toUpperCase()}
                          </div>
                          <div className="min-w-0">
                            <h4 className="text-xs font-bold text-slate-900 truncate flex items-center gap-1.5">
                              {chan.name}
                            </h4>
                            <p className="text-[11px] text-slate-500 font-medium truncate">
                              {chan.handle ? `@${chan.handle}` : plat.name}
                            </p>
                          </div>
                        </div>

                        {/* Actions */}
                        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition">
                          <button
                            onClick={(e) => handleOpenEditChannel(chan, e)}
                            className="p-1 hover:bg-slate-200/70 rounded-md text-slate-500 hover:text-slate-900"
                            title="Sửa thông tin kênh"
                          >
                            <Edit3 className="w-3 h-3" />
                          </button>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setDeleteChannelTarget(chan);
                            }}
                            className="p-1 hover:bg-rose-100 rounded-md text-slate-400 hover:text-rose-600"
                            title="Xóa kênh"
                          >
                            <Trash2 className="w-3 h-3" />
                          </button>
                        </div>
                      </div>

                      {/* Tags list */}
                      {chan.tags && chan.tags.length > 0 && (
                        <div className="flex items-center gap-1 flex-wrap mt-2.5">
                          {chan.tags.slice(0, 3).map((tag, idx) => (
                            <span
                              key={idx}
                              className="px-2 py-0.5 rounded-md text-[10px] font-bold bg-white text-slate-600 border border-slate-200/80 shadow-2xs"
                            >
                              #{tag}
                            </span>
                          ))}
                          {chan.tags.length > 3 && (
                            <span className="text-[9px] text-slate-400 font-bold">
                              +{chan.tags.length - 3}
                            </span>
                          )}
                        </div>
                      )}

                      {/* Footer Stats */}
                      <div className="flex items-center justify-between text-[11px] font-semibold text-slate-500 mt-3 pt-2.5 border-t border-slate-200/50">
                        <span className={`px-2 py-0.5 rounded-md text-[10px] font-bold border ${plat.color}`}>
                          {plat.name}
                        </span>
                        <div className="flex items-center gap-2">
                          <span className="text-slate-600">{chan.video_count || 0} video</span>
                          {(chan.overlays || []).length > 0 && (
                            <span className="text-indigo-600 bg-indigo-50 px-1.5 py-0.2 rounded font-bold">
                              {chan.overlays.length} khung/logo
                            </span>
                          )}
                          {chan.ready_count > 0 && (
                            <span className="text-emerald-600 bg-emerald-50 px-1.5 py-0.2 rounded font-bold">
                              {chan.ready_count} sẵn sàng
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>

        {/* Right Column: Selected Channel Content Hub & Videos */}
        <div className="lg:col-span-8 space-y-4">
          {activeChannel ? (
            <div className="bg-white rounded-3xl border border-slate-200/90 p-5 md:p-6 shadow-xs space-y-5">
              {/* Channel Header & Summary */}
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-slate-100">
                <div className="flex items-center gap-3">
                  <div className={`w-12 h-12 rounded-2xl flex items-center justify-center font-black text-sm text-white shadow-sm ${
                    activeChannel.color === 'purple' ? 'bg-purple-600' :
                    activeChannel.color === 'emerald' ? 'bg-emerald-600' :
                    activeChannel.color === 'rose' ? 'bg-rose-600' :
                    activeChannel.color === 'amber' ? 'bg-amber-600' :
                    activeChannel.color === 'indigo' ? 'bg-indigo-600' : 'bg-blue-600'
                  }`}>
                    {activeChannel.name.slice(0, 2).toUpperCase()}
                  </div>
                  <div>
                    <h3 className="text-base md:text-lg font-extrabold text-slate-900 flex items-center gap-2">
                      {activeChannel.name}
                      <span className="text-xs font-semibold text-slate-500">
                        {activeChannel.handle ? `@${activeChannel.handle}` : ''}
                      </span>
                    </h3>
                    <p className="text-xs text-slate-500 font-medium mt-0.5">
                      {activeChannel.description || 'Chưa có mô tả kênh.'}
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-2 flex-wrap">
                  <button
                    onClick={handleOpenAssignModal}
                    className="px-4 py-2.5 bg-blue-600 hover:bg-blue-700 active:scale-95 text-white text-xs font-extrabold rounded-xl transition shadow-sm flex items-center gap-1.5 cursor-pointer"
                  >
                    <FolderPlus className="w-3.5 h-3.5 stroke-[2.5]" /> Gán Video Từ Kho
                  </button>
                </div>
              </div>

              <ChannelOverlayEditor
                channel={activeChannel}
                previewSrc={
                  videos[0]?.job_id
                    ? getMediaUrl(`/api/v1/stream/${videos[0].job_id}`)
                    : null
                }
                onOverlaysChange={(overlays) => {
                  setChannels((prev) =>
                    prev.map((c) =>
                      c.channel_id === activeChannel.channel_id ? { ...c, overlays } : c
                    )
                  );
                }}
                onToast={setToast}
              />

              {/* Channel Tags & Search / Filter Controls */}
              <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3">
                {/* Search Box */}
                <div className="relative flex-1">
                  <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Tìm theo tiêu đề, hashtag, nhãn video..."
                    className="w-full pl-9 pr-3 py-2 bg-slate-50 border border-slate-200 rounded-xl text-xs font-medium text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition"
                  />
                  {searchQuery && (
                    <button
                      onClick={() => setSearchQuery('')}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  )}
                </div>

                {/* Status Filter Tabs */}
                <div className="flex items-center gap-1 p-1 bg-slate-100 rounded-xl">
                  {['ALL', 'READY', 'PUBLISHED', 'DRAFT'].map((st) => (
                    <button
                      key={st}
                      onClick={() => setStatusFilter(st)}
                      className={`px-3 py-1.5 rounded-lg text-xs font-bold transition ${
                        statusFilter === st
                          ? 'bg-white text-slate-900 shadow-2xs'
                          : 'text-slate-500 hover:text-slate-800'
                      }`}
                    >
                      {st === 'ALL' ? 'Tất Cả' : st === 'READY' ? 'Sẵn Sàng' : st === 'PUBLISHED' ? 'Đã Đăng' : 'Bản Nháp'}
                    </button>
                  ))}
                </div>
              </div>

              {/* Active Channel Tags for Quick Filtering */}
              {activeChannel.tags && activeChannel.tags.length > 0 && (
                <div className="flex items-center gap-1.5 flex-wrap pt-1">
                  <span className="text-[11px] font-bold text-slate-400 flex items-center gap-1">
                    <Tag className="w-3 h-3" /> Nhãn kênh:
                  </span>
                  <button
                    onClick={() => setTagFilter('')}
                    className={`px-2 py-0.5 rounded-md text-[10px] font-bold transition ${
                      tagFilter === ''
                        ? 'bg-blue-600 text-white'
                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                    }`}
                  >
                    Tất cả nhãn
                  </button>
                  {activeChannel.tags.map((t, idx) => (
                    <button
                      key={idx}
                      onClick={() => setTagFilter(tagFilter === t ? '' : t)}
                      className={`px-2 py-0.5 rounded-md text-[10px] font-bold transition ${
                        tagFilter === t
                          ? 'bg-blue-600 text-white'
                          : 'bg-blue-50 text-blue-700 hover:bg-blue-100 border border-blue-200/60'
                      }`}
                    >
                      #{t}
                    </button>
                  ))}
                </div>
              )}

              {/* Videos Content List */}
              <div className="space-y-3 pt-2">
                {loadingVideos ? (
                  <div className="text-center py-16 text-slate-400 text-xs flex items-center justify-center gap-2 font-medium">
                    <Loader2 className="w-4 h-4 animate-spin text-blue-600" /> Đang nạp danh sách video...
                  </div>
                ) : filteredVideos.length === 0 ? (
                  <div className="text-center py-16 text-slate-400 text-xs rounded-2xl border border-dashed border-slate-200 bg-slate-50/50">
                    <Video className="w-10 h-10 text-slate-300 mx-auto mb-2" />
                    <p className="font-bold text-slate-700 text-sm">Chưa có video nào trong kênh này</p>
                    <p className="text-slate-400 mt-1 max-w-md mx-auto">
                      Hãy bấm "Gán Video Từ Kho" ở trên để chọn video thành phẩm đã Reup gán vào kênh, thêm tiêu đề, caption và sẵn sàng xuất bản.
                    </p>
                  </div>
                ) : (
                  filteredVideos.map((vid) => {
                    const videoSrc = getMediaUrl(`/api/v1/stream/${vid.job_id}`);
                    const isPublished = vid.publish_status === 'PUBLISHED';
                    const isReady = vid.publish_status === 'READY';

                    return (
                      <div
                        key={vid.id}
                        className="p-4 rounded-2xl border border-slate-200/90 bg-white hover:border-blue-300 transition-all shadow-xs space-y-3"
                      >
                        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
                          {/* Title & Preview Trigger */}
                          <div className="flex items-center gap-3 min-w-0">
                            <button
                              onClick={() => setPreviewVideo(videoSrc)}
                              className="w-10 h-10 rounded-xl bg-slate-900 hover:bg-blue-600 text-white flex items-center justify-center shrink-0 transition shadow-xs group"
                              title="Xem thử video"
                            >
                              <Play className="w-4 h-4 ml-0.5 fill-current group-hover:scale-110 transition" />
                            </button>
                            <div className="min-w-0">
                              <h4 className="text-xs sm:text-sm font-extrabold text-slate-900 truncate">
                                {vid.title || 'Video Không Tiêu Đề'}
                              </h4>
                              <p className="text-[11px] text-slate-400 font-medium flex items-center gap-2 mt-0.5">
                                <span>Mã Job: {vid.job_id}</span>
                                <span>•</span>
                                <span>Tạo lúc: {vid.created_at?.slice(0, 16).replace('T', ' ')}</span>
                              </p>
                            </div>
                          </div>

                          {/* Quick Publish Status Switcher */}
                          <div className="flex items-center gap-2 shrink-0">
                            <select
                              value={vid.publish_status}
                              onChange={(e) => handleQuickStatusChange(vid, e.target.value)}
                              className={`px-2.5 py-1 rounded-xl text-xs font-bold border focus:outline-none cursor-pointer ${
                                isPublished
                                  ? 'bg-purple-50 text-purple-700 border-purple-200'
                                  : isReady
                                  ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                                  : 'bg-amber-50 text-amber-700 border-amber-200'
                              }`}
                            >
                              <option value="DRAFT">📝 Bản Nháp</option>
                              <option value="READY">🚀 Sẵn Sàng Đăng</option>
                              <option value="PUBLISHED">✅ Đã Xuất Bản</option>
                            </select>

                            <button
                              onClick={() => handleCopyCaption(vid)}
                              className="px-2.5 py-1 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-xl text-xs font-bold transition flex items-center gap-1"
                              title="Sao chép toàn bộ tiêu đề, caption và hashtag"
                            >
                              {copiedId === vid.id ? (
                                <Check className="w-3.5 h-3.5 text-emerald-600" />
                              ) : (
                                <Copy className="w-3.5 h-3.5 text-slate-500" />
                              )}
                              <span>Copy Post</span>
                            </button>

                            <button
                              onClick={() => handleOpenEditVideo(vid)}
                              className="p-1.5 hover:bg-slate-100 text-slate-400 hover:text-slate-700 rounded-lg transition"
                              title="Chỉnh sửa nội dung"
                            >
                              <Edit3 className="w-3.5 h-3.5" />
                            </button>
                            <button
                              onClick={() => setDeleteVideoTarget(vid)}
                              className="p-1.5 hover:bg-rose-50 text-slate-400 hover:text-rose-600 rounded-lg transition"
                              title="Xóa khỏi kênh"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </div>

                        {/* Caption & Hashtags preview */}
                        {vid.caption && (
                          <div className="p-2.5 bg-slate-50/80 rounded-xl border border-slate-100 text-xs text-slate-700 font-medium whitespace-pre-wrap">
                            {vid.caption}
                          </div>
                        )}

                        {/* Video Tags */}
                        {vid.tags && vid.tags.length > 0 && (
                          <div className="flex items-center gap-1.5 flex-wrap">
                            {vid.tags.map((t, idx) => (
                              <span
                                key={idx}
                                className="px-2 py-0.5 rounded-md text-[10px] font-bold bg-blue-50 text-blue-700 border border-blue-200/60"
                              >
                                #{t}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          ) : (
            <div className="bg-white rounded-3xl border border-slate-200/90 p-12 text-center text-slate-400 space-y-3">
              <Tv2 className="w-12 h-12 text-slate-300 mx-auto" />
              <h3 className="text-base font-extrabold text-slate-800">Chưa Chọn Kênh Quản Lý</h3>
              <p className="text-xs max-w-sm mx-auto text-slate-500 font-medium">
                Vui lòng chọn 1 kênh từ danh sách bên trái hoặc tạo kênh mới để bắt đầu gắn nhãn và quản lý video nội dung.
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Modal: Create / Edit Channel */}
      {showChannelModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-xs animate-in fade-in duration-200">
          <div className="bg-white rounded-3xl border border-slate-200 max-w-md w-full p-6 shadow-2xl space-y-5">
            <div className="flex items-center justify-between pb-3 border-b border-slate-100">
              <h3 className="text-sm font-extrabold text-slate-900 flex items-center gap-2">
                <Tv2 className="w-4 h-4 text-blue-600" />
                {editingChannel ? 'Cập Nhật Thông Tin Kênh' : 'Tạo Kênh Phân Phối Mới'}
              </h3>
              <button
                onClick={() => setShowChannelModal(false)}
                className="text-slate-400 hover:text-slate-600 p-1 rounded-lg"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleSaveChannel} className="space-y-4 text-xs">
              <div>
                <label className="block font-bold text-slate-700 mb-1">Tên Kênh *</label>
                <input
                  type="text"
                  required
                  value={channelForm.name}
                  onChange={(e) => setChannelForm({ ...channelForm, name: e.target.value })}
                  placeholder="Ví dụ: Kênh Review Phim VIP, Daily Viral TikTok..."
                  className="w-full px-3.5 py-2.5 bg-slate-50 border border-slate-200 rounded-xl font-semibold text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block font-bold text-slate-700 mb-1">Nền Tảng</label>
                  <select
                    value={channelForm.platform}
                    onChange={(e) => setChannelForm({ ...channelForm, platform: e.target.value })}
                    className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl font-bold text-slate-800 focus:outline-none focus:border-blue-500"
                  >
                    {PLATFORMS.map((p) => (
                      <option key={p.id} value={p.id}>{p.name}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block font-bold text-slate-700 mb-1">ID Kênh / Handle</label>
                  <input
                    type="text"
                    value={channelForm.handle}
                    onChange={(e) => setChannelForm({ ...channelForm, handle: e.target.value })}
                    placeholder="@username"
                    className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl font-semibold text-slate-900 focus:outline-none focus:border-blue-500"
                  />
                </div>
              </div>

              <div>
                <label className="block font-bold text-slate-700 mb-1">
                  Nhãn / Chủ Đề Kênh (cách nhau bởi dấu phẩy)
                </label>
                <input
                  type="text"
                  value={channelForm.tagsInput}
                  onChange={(e) => setChannelForm({ ...channelForm, tagsInput: e.target.value })}
                  placeholder="Hài Hước, Review Phim, Tin Nhanh, Đời Sống..."
                  className="w-full px-3.5 py-2.5 bg-slate-50 border border-slate-200 rounded-xl font-semibold text-slate-900 focus:outline-none focus:border-blue-500"
                />
              </div>

              <div>
                <label className="block font-bold text-slate-700 mb-1">Màu Nhận Diện Kênh</label>
                <div className="flex items-center gap-2">
                  {COLOR_THEMES.map((c) => (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => setChannelForm({ ...channelForm, color: c.id })}
                      className={`w-7 h-7 rounded-xl ${c.bg} transition-all flex items-center justify-center ${
                        channelForm.color === c.id ? 'ring-2 ring-offset-2 ring-slate-900 scale-110' : 'opacity-70 hover:opacity-100'
                      }`}
                    >
                      {channelForm.color === c.id && <Check className="w-3.5 h-3.5 text-white stroke-[3]" />}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="block font-bold text-slate-700 mb-1">Mô Tả Kênh</label>
                <textarea
                  rows={2}
                  value={channelForm.description}
                  onChange={(e) => setChannelForm({ ...channelForm, description: e.target.value })}
                  placeholder="Mô tả chiến lược nội dung, phong cách biên tập..."
                  className="w-full px-3.5 py-2 bg-slate-50 border border-slate-200 rounded-xl font-medium text-slate-900 focus:outline-none focus:border-blue-500"
                />
              </div>

              <div className="flex items-center justify-end gap-2 pt-3 border-t border-slate-100">
                <button
                  type="button"
                  onClick={() => setShowChannelModal(false)}
                  className="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold rounded-xl transition"
                >
                  Hủy
                </button>
                <button
                  type="submit"
                  className="px-5 py-2 bg-blue-600 hover:bg-blue-700 active:scale-95 text-white font-extrabold rounded-xl transition shadow-sm"
                >
                  {editingChannel ? 'Cập Nhật' : 'Tạo Kênh'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Modal: Assign Video From Outputs / Edit Video Content */}
      {showAssignModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-xs animate-in fade-in duration-200">
          <div className="bg-white rounded-3xl border border-slate-200 max-w-lg w-full p-6 shadow-2xl space-y-4 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between pb-3 border-b border-slate-100">
              <h3 className="text-sm font-extrabold text-slate-900 flex items-center gap-2">
                <FolderPlus className="w-4 h-4 text-blue-600" />
                {editingVideo ? 'Chỉnh Sửa Nội Dung Video' : `Gán Video Vào Kênh: ${activeChannel?.name}`}
              </h3>
              <button
                onClick={() => setShowAssignModal(false)}
                className="text-slate-400 hover:text-slate-600 p-1 rounded-lg"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleSaveVideoContent} className="space-y-4 text-xs">
              {/* If creating new assignment: select from available outputs */}
              {!editingVideo && (
                <div>
                  <label className="block font-bold text-slate-700 mb-1">
                    Chọn Video Thành Phẩm ({availableOutputs.length} video) *
                  </label>
                  {availableOutputs.length === 0 ? (
                    <p className="text-rose-500 font-bold p-3 bg-rose-50 rounded-xl">
                      Chưa có video thành phẩm nào trong kho. Hãy tạo và hoàn thành job xử lý trước!
                    </p>
                  ) : (
                    <select
                      value={selectedOutput?.job_id}
                      onChange={(e) => {
                        const out = availableOutputs.find((o) => o.job_id === e.target.value);
                        setSelectedOutput(out);
                      }}
                      className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl font-bold text-slate-800 focus:outline-none focus:border-blue-500"
                    >
                      {availableOutputs.map((o) => (
                        <option key={o.job_id} value={o.job_id}>
                          {o.job_id} - {(o.file_size / (1024 * 1024)).toFixed(2)} MB ({o.created_at?.slice(0, 16)})
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              )}

              <div>
                <label className="block font-bold text-slate-700 mb-1">Tiêu Đề Video Đăng Bài *</label>
                <input
                  type="text"
                  required
                  value={videoForm.title}
                  onChange={(e) => setVideoForm({ ...videoForm, title: e.target.value })}
                  placeholder="Tiêu đề bắt mắt, giật tít, chuẩn SEO..."
                  className="w-full px-3.5 py-2.5 bg-slate-50 border border-slate-200 rounded-xl font-semibold text-slate-900 focus:outline-none focus:border-blue-500"
                />
              </div>

              <div>
                <label className="block font-bold text-slate-700 mb-1">Nội Dung Caption / Bài Viết</label>
                <textarea
                  rows={3}
                  value={videoForm.caption}
                  onChange={(e) => setVideoForm({ ...videoForm, caption: e.target.value })}
                  placeholder="Mô tả nội dung, kêu gọi hành động (CTA), liên kết bio..."
                  className="w-full px-3.5 py-2.5 bg-slate-50 border border-slate-200 rounded-xl font-medium text-slate-900 focus:outline-none focus:border-blue-500"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block font-bold text-slate-700 mb-1">Hashtags / Nhãn Video</label>
                  <input
                    type="text"
                    value={videoForm.tagsInput}
                    onChange={(e) => setVideoForm({ ...videoForm, tagsInput: e.target.value })}
                    placeholder="viral, review, trend..."
                    className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl font-semibold text-slate-900 focus:outline-none focus:border-blue-500"
                  />
                </div>
                <div>
                  <label className="block font-bold text-slate-700 mb-1">Trạng Thái Đăng</label>
                  <select
                    value={videoForm.publish_status}
                    onChange={(e) => setVideoForm({ ...videoForm, publish_status: e.target.value })}
                    className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl font-bold text-slate-800 focus:outline-none focus:border-blue-500"
                  >
                    <option value="DRAFT">📝 Bản Nháp (Draft)</option>
                    <option value="READY">🚀 Sẵn Sàng Đăng (Ready)</option>
                    <option value="PUBLISHED">✅ Đã Xuất Bản (Published)</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="block font-bold text-slate-700 mb-1">Ghi Chú Nội Bộ</label>
                <input
                  type="text"
                  value={videoForm.notes}
                  onChange={(e) => setVideoForm({ ...videoForm, notes: e.target.value })}
                  placeholder="Ghi chú thời gian đăng, kịch bản, âm nhạc..."
                  className="w-full px-3.5 py-2 bg-slate-50 border border-slate-200 rounded-xl font-medium text-slate-900 focus:outline-none focus:border-blue-500"
                />
              </div>

              <div className="flex items-center justify-end gap-2 pt-3 border-t border-slate-100">
                <button
                  type="button"
                  onClick={() => setShowAssignModal(false)}
                  className="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold rounded-xl transition"
                >
                  Hủy
                </button>
                <button
                  type="submit"
                  className="px-5 py-2 bg-blue-600 hover:bg-blue-700 active:scale-95 text-white font-extrabold rounded-xl transition shadow-sm"
                >
                  {editingVideo ? 'Lưu Thay Đổi' : 'Gán Vào Kênh'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Delete Channel Confirmation Modal */}
      {deleteChannelTarget && (
        <ConfirmModal
          isOpen={true}
          title="Xác Nhận Xóa Kênh"
          message={`Bạn có chắc chắn muốn xóa kênh "${deleteChannelTarget.name}" không? Tất cả các video đã gán vào kênh này sẽ bị hủy liên kết.`}
          confirmLabel="Xóa Kênh"
          confirmVariant="danger"
          onConfirm={handleDeleteChannelConfirm}
          onCancel={() => setDeleteChannelTarget(null)}
        />
      )}

      {/* Delete Video Assignment Confirmation Modal */}
      {deleteVideoTarget && (
        <ConfirmModal
          isOpen={true}
          title="Gỡ Video Khỏi Kênh"
          message={`Bạn có chắc chắn muốn gỡ video "${deleteVideoTarget.title || deleteVideoTarget.job_id}" khỏi kênh này không? File video trong kho thành phẩm vẫn được giữ nguyên.`}
          confirmLabel="Gỡ Khỏi Kênh"
          confirmVariant="danger"
          onConfirm={handleDeleteVideoConfirm}
          onCancel={() => setDeleteVideoTarget(null)}
        />
      )}

      {/* Video Modal Player */}
      {previewVideo && (
        <VideoModal
          isOpen={true}
          videoSrc={previewVideo}
          title="Xem Thử Video Thành Phẩm"
          onClose={() => setPreviewVideo(null)}
        />
      )}

      {/* Toast Notification */}
      {toast && (
        <Toast
          type={toast.type}
          title={toast.title}
          message={toast.message}
          onClose={() => setToast(null)}
        />
      )}
    </div>
  );
}
